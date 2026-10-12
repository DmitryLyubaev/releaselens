"""Owner-run WSL measurements. Hidden cookies, fixed labels, guarded files."""
from datetime import datetime,timezone
from pathlib import Path
import getpass,json,logging,os,re,subprocess,sys,time
import httpx
from app.gateway import freeze
from app.gateway.metric import SCOPE
from . import observations,report
from .contracts import Observation,ContractRefused,CheckResult
from .checks import evaluate,result_from_dict,observation_from_dict,offline_edges
from .http_client import WebAppClient,_unique
from .private_az import PrivateAz,TransportRefused
from .release_cli import GitHubMetadata,_canonical
from .release_transport import AzureReleaseTransport,ARM_SCOPE
from . import evidence

OUT=freeze.REPO_ROOT/'eval/reports/webapp'
NAMES={'owner':'W1','access':'W2','refresh':'W3','settings':'W6a','teardown':'W6b'}

def _load(path):
    if path.stat().st_size>1024*1024: raise ContractRefused()
    return json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=_unique)

def _commit():
    done=subprocess.run(['git','rev-parse','HEAD'],cwd=freeze.REPO_ROOT,capture_output=True,text=True,check=True)
    return done.stdout.strip()

def _write_result(out,result,forbidden):
    report.write(out/f'check-{result.check.lower()}.json',json.dumps(result.to_dict(),sort_keys=True,indent=2)+'\n',forbidden)

def validate_refs(refs,subscription):
    if not isinstance(refs,dict) or set(refs)!={'webapp_group','gateway_group','plan','site','slot','deleted_apim','bootstrap'}: raise TransportRefused('invalid_response')
    prefix='/subscriptions/'+re.escape(subscription)
    group=prefix+r'/resourceGroups/[A-Za-z0-9_.()-]{1,90}'
    patterns={'webapp_group':group,'gateway_group':group,'plan':group+r'/providers/Microsoft.Web/serverfarms/[A-Za-z0-9-]{1,60}',
              'site':group+r'/providers/Microsoft.Web/sites/[A-Za-z0-9-]{1,60}','slot':group+r'/providers/Microsoft.Web/sites/[A-Za-z0-9-]{1,60}/slots/staging',
              'deleted_apim':prefix+r'/providers/Microsoft.ApiManagement/locations/australiaeast/deletedservices/[A-Za-z0-9-]{1,50}'}
    if any(not isinstance(refs[key],str) or not re.fullmatch(pattern,value:=refs[key]) for key,pattern in patterns.items()): raise TransportRefused('invalid_response')
    if not isinstance(refs['bootstrap'],list) or not refs['bootstrap'] or any(not isinstance(value,str) or not re.fullmatch(group,value) for value in refs['bootstrap']): raise TransportRefused('invalid_response')
    if any(not refs[key].startswith(refs['webapp_group']+'/') for key in ('plan','site','slot')) or refs['slot']!=refs['site']+'/slots/staging' or refs['gateway_group']==refs['webapp_group'] or any(value in {refs['webapp_group'],refs['gateway_group']} for value in refs['bootstrap']): raise TransportRefused('invalid_response')
    return refs

def _artifact(api,run,kind,out):
    name=f"webapp-{kind}-{run['id']}-{run['run_attempt']}"
    return evidence.artifact(api,run,name,out/(name+'.zip'))

def load_release(api,values,commit,out):
    try:
        stage=api.source(int(values['WEBAPP_STAGE_RUN_ID'])); promotion=api.source(int(values['WEBAPP_PROMOTION_RUN_ID']))
        if promotion['head_sha']!=commit: raise ValueError()
        repo=api.repository
        data=dict(repository=repo,commit=commit,stage=stage,promotion=promotion)
        if stage['conclusion']=='failure':
            recorded=_artifact(api,stage,'observations',out)['observation-W5.json']
            evidence.observation(recorded,stage,repo,mode='stage',fixture=True)
            rejected=_artifact(api,promotion,'rejection',out)['rejection.json']
            evidence.rejection(rejected,promotion,stage,repo)
            data.update(observation=recorded,rejection=rejected,ci={})
            return {},data,None
        from .release import validate_source
        receipt=_artifact(api,stage,'stage',out)['receipt.json']; ci=api.ci(stage['head_sha'])
        if not validate_source(stage,receipt,repo,ci): raise ValueError()
        records=_artifact(api,promotion,'observations',out)
        w4=records['observation-W4.json']; settings=records['observation-W6a.json']
        evidence.observation(w4,promotion,repo,mode='promote',fixture=False,source_run=stage)
        selected=evidence.observation(settings,promotion,repo,mode='promote',fixture=False,source_run=stage)
        data.update(observation=w4,ci=ci)
        # Settings evidence requires the corresponding successful, bound promotion facts.
        measured=observations.release(receipt,data)
        passed=all(measured.matches.get(key) is True for key in ('baseline_healthy','staging_healthy','approval_recorded','swap_succeeded','main_candidate','staging_baseline','slot_labels_retained'))
        return receipt,data,selected if passed else None
    except Exception: raise TransportRefused('invalid_response') from None

class _OwnerConfiguration(AzureReleaseTransport):
    def _arm(self,method,url,body=None):
        from urllib.parse import urlsplit
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc!='management.azure.com' or not (parsed.path==self._resource or parsed.path.startswith(self._resource+'/')) or method not in {'GET','POST'} or method=='POST' and not parsed.path.endswith('/config/appsettings/list'): raise TransportRefused('account_refused')
        return self.az.call(['rest','--method',method,'--url',url])
    def _read(self,method,url,**kwargs): return self._arm(method,url),{}

def _context(mode,values,commit,out):
    if sys.platform!='linux': raise TransportRefused('account_refused')
    http=httpx.Client(timeout=15,follow_redirects=False)
    try:
        if mode=='release':
            from types import SimpleNamespace
            api=GitHubMetadata(http,values['GITHUB_REPOSITORY'],values.get('GH_TOKEN'))
            receipt,data,_=load_release(api,values,commit,out)
            return SimpleNamespace(receipt=receipt,source_metadata=data,http=http)
        az=PrivateAz(values['AZURE_TENANT_ID'],values['AZURE_SUBSCRIPTION_ID'],values['WEBAPP_OWNER_PRINCIPAL'])
        az.call(['account','show'])  # Guard before cookies, credential construction or token acquisition.
        from app.retrieval.azure_auth import TokenSource
        metric=TokenSource(SCOPE,values['AZURE_TENANT_ID']); arm=TokenSource(ARM_SCOPE,values['AZURE_TENANT_ID'])
        now=lambda:datetime.now(timezone.utc)
        main=staging=None
        if mode in {'owner','refresh','settings'}:
            if mode=='settings':
                api=GitHubMetadata(http,values['GITHUB_REPOSITORY'],values.get('GH_TOKEN'))
                _,_,promotion_settings=load_release(api,values,commit,out)
                if promotion_settings is None: raise TransportRefused('invalid_response')
                if input('Fresh owner sign-in completed on main? Type fresh-main: ').strip()!='fresh-main': raise TransportRefused('account_refused')
            main=WebAppClient(http,values['WEBAPP_MAIN_URL'],getpass.getpass('Main Easy Auth Cookie header (hidden): '))
            if mode=='settings':
                if input('Fresh owner sign-in completed on staging? Type fresh-staging: ').strip()!='fresh-staging': raise TransportRefused('account_refused')
                staging=WebAppClient(http,values['WEBAPP_STAGING_URL'],getpass.getpass('Staging Easy Auth Cookie header (hidden): '))
        ctx=observations.LiveContext(main,staging,az,values['WEBAPP_OWNER_OBJECT_ID'],values.get('WEBAPP_APPINSIGHTS_APP_ID',''),metric.token,http,now,time.sleep,lambda:print('status=waiting',flush=True))
        if mode=='access':
            ctx.anonymous_main=WebAppClient(http,values['WEBAPP_MAIN_URL']); ctx.anonymous_staging=WebAppClient(http,values['WEBAPP_STAGING_URL'])
            api=GitHubMetadata(http,values['GITHUB_REPOSITORY'],values.get('GH_TOKEN')); metadata=api.ci(commit)
            metadata['jobs']=api._get(f"actions/runs/{metadata['id']}/attempts/{metadata['run_attempt']}/jobs?per_page=100")['jobs']
            ctx.offline_edges_passed=offline_edges(metadata,commit,values['GITHUB_REPOSITORY'])
            run=api.source(int(values['WEBAPP_DEPLOYMENT_RUN_ID']))
            title=run.get('display_title','')
            if run['head_sha']!=commit or run['head_branch']!='main' or run['path']!='.github/workflows/webapp-release.yml' or run['repository']['full_name']!=values['GITHUB_REPOSITORY'] or run['conclusion']!='success' or run['status']!='completed' or not (title==f"webapp-seed-candidate-{commit}-{run['run_attempt']}" or title==f"webapp-stage-candidate-{commit}-{run['run_attempt']}"): raise TransportRefused('invalid_response')
            if not any(job.get('name')=='release' and job.get('status')=='completed' and job.get('conclusion')=='success' for job in run['jobs']): raise TransportRefused('invalid_response')
            record=_artifact(api,run,'observations',out)['observation-W2.json']
            ctx.deployment_observation=evidence.observation(record,run,values['GITHUB_REPOSITORY'],mode='seed' if title.startswith('webapp-seed-') else 'stage',fixture=False)
        if mode=='settings':
            intended=_canonical(json.loads(values['WEBAPP_INTENDED_CONFIG']))
            reader=_OwnerConfiguration(http,None,values['AZURE_TENANT_ID'],values['AZURE_SUBSCRIPTION_ID'],'','',values['WEBAPP_RESOURCE_ID'],values['WEBAPP_MAIN_URL'],values['WEBAPP_STAGING_URL']); reader.az=az
            ctx.configuration=lambda:_canonical(reader.configuration(intended)); ctx.intended_configuration=intended
            ctx.fresh_main=ctx.fresh_staging=True
            ctx.promotion_settings=promotion_settings
        if mode=='teardown':
            refs=validate_refs(json.loads(values['WEBAPP_TEARDOWN_REFS']),values['AZURE_SUBSCRIPTION_ID'])
            ctx.resources=refs
            baseline=json.loads(values['WEBAPP_BOOTSTRAP_BASELINE'])
            if not isinstance(baseline,dict) or set(baseline)!=set(refs['bootstrap']) or any(not isinstance(ids,list) or not ids or any(not isinstance(item,str) or not item.startswith(group+'/providers/') for item in ids) for group,ids in baseline.items()): raise ContractRefused()
            ctx.bootstrap_baseline=baseline
            def inventory(group):
                data=az.call(['rest','--method','GET','--url','https://management.azure.com'+group+'/resources?api-version=2021-04-01'])
                if data.get('nextLink') or not isinstance(data.get('value'),list): raise TransportRefused('invalid_response')
                return [item['id'] for item in data['value']]
            ctx.inventory=inventory
            def lookup(resource):
                prefix='/subscriptions/'+values['AZURE_SUBSCRIPTION_ID']+'/'
                if not isinstance(resource,str) or not resource.startswith(prefix) or '?' in resource or '#' in resource or '..' in resource: raise TransportRefused('invalid_response')
                az.call(['account','show']); bearer=arm.token()
                api='2024-05-01' if '/deletedservices/' in resource else '2024-11-01' if '/Microsoft.Web/' in resource else '2021-04-01'
                request=http.build_request('GET','https://management.azure.com'+resource+'?api-version='+api,headers={'Authorization':'Bearer '+bearer},timeout=15)
                request.headers.pop('Cookie',None)
                response=http.send(request,stream=True,follow_redirects=False,auth=None)
                try: return response.status_code
                finally: response.close()
            ctx.lookup=lookup
        return ctx
    except BaseException:
        http.close(); raise

def main(argv=None,*,env=None,clean_guard=None,output_guard=None,out=OUT,commit_reader=None,context_factory=None,operations=None)->int:
    logging_before=(logging.root.manager.disable,logging.lastResort)
    logging.disable(logging.CRITICAL); logging.lastResort=None
    argv=sys.argv[1:] if argv is None else argv
    ctx=None; started=False; check=None; forbidden=[]; commit=None
    try:
        (clean_guard or freeze.require_clean_tree)(); (output_guard or freeze.require_output_ignored)(out)
        if len(argv)!=1 or argv[0] not in {*NAMES,'release','report'}: raise ContractRefused()
        mode=argv[0]; commit=(commit_reader or _commit)()
        if not re.fullmatch(r'[a-f0-9]{40}',commit): raise ContractRefused()
        if mode=='report':
            results=[result_from_dict(_load(out/f'check-{name.lower()}.json')) for name in report.ORDER if (out/f'check-{name.lower()}.json').exists()]
            report.write(out/'report.md',report.render(results,commit),forbidden)
            print('status=ok'); return 0
        values=os.environ if env is None else env
        forbidden=[value for key,value in values.items() if key in {'AZURE_TENANT_ID','AZURE_SUBSCRIPTION_ID','AZURE_CLIENT_ID','WEBAPP_OWNER_PRINCIPAL','WEBAPP_OWNER_OBJECT_ID','WEBAPP_MAIN_URL','WEBAPP_STAGING_URL','WEBAPP_RESOURCE_ID','WEBAPP_APPINSIGHTS_APP_ID'} and value]
        ctx=(context_factory or _context)(mode,values,commit,out)
        check=NAMES.get(mode,'W4')
        if mode=='release': check='W5' if ctx.source_metadata['observation']['observation']['check']=='W5' else 'W4'
        def pending(observation): report.write(out/f'observation-{observation.check.lower()}-pending.json',json.dumps(observation.to_dict(),sort_keys=True)+'\n',forbidden)
        if isinstance(ctx,observations.LiveContext): ctx.persist=pending
        started=True
        defaults={'owner':observations.owner,'access':observations.access,'refresh':observations.refresh,'teardown':observations.teardown,
                  'settings':lambda context:observations.settings(context,context.promotion_settings),
                  'release':lambda context:observations.release(context.receipt,context.source_metadata)}
        measured=(operations or defaults)[mode](ctx)
        kind='mixed' if check in {'W2','W6a'} else 'workflow' if check in {'W4','W5'} else 'live'
        result=evaluate(check,measured,commit=commit,evidence=kind,measured_at=observations.stamp(datetime.now(timezone.utc)))
        _write_result(out,result,forbidden)
        print('status='+result.verdict); return 0 if result.verdict=='pass' else 1
    except (Exception,KeyboardInterrupt):
        if started and check:
            try:
                partial=getattr(ctx,'last_observation',None) or Observation(check)
                result=CheckResult(check,'fail','mixed' if check in {'W2','W6a'} else 'workflow' if check in {'W4','W5'} else 'live',(partial,),commit,observations.stamp(datetime.now(timezone.utc)))
                _write_result(out,result,forbidden); print('status=fail'); return 1
            except Exception: pass
        print('status=refused'); return 1
    finally:
        if ctx is not None:
            for name in ('main','staging','anonymous_main','anonymous_staging'):
                client=getattr(ctx,name,None)
                if client is not None and hasattr(client,'_cookies'): client._cookies.clear()
            http=getattr(ctx,'http',None)
            if http is not None: http.close()
        ctx=None
        logging.lastResort=logging_before[1]; logging.disable(logging_before[0])

if __name__=='__main__': raise SystemExit(main())
