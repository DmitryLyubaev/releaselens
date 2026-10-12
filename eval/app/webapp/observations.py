"""Measurements, with private values and partial facts held only in memory."""
from dataclasses import dataclass,field
from datetime import datetime,timezone,timedelta
import base64,json
from collections.abc import Callable
from .contracts import Observation,REQUIRED_MATCHES
from .private_az import TransportRefused
from .metrics import query_window,owner_matches
from .release import validate_source,WORKFLOW
from .checks import observation_from_dict

QUESTION='Explain why a staging slot is useful when releasing a web app.'
def stamp(value): return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')

@dataclass(repr=False)
class LiveContext:
    main:object
    staging:object
    az:object
    owner_oid:str
    app_id:str
    metric_token:Callable
    http:object
    now:Callable
    sleep:Callable
    progress:Callable
    anonymous_main:object=None
    anonymous_staging:object=None
    deployment_bearer:Callable|None=None
    deployment_observation:Observation|None=None
    offline_edges_passed:bool=False
    configuration:Callable|None=None
    intended_configuration:dict=field(default_factory=dict)
    fresh_main:bool=False
    fresh_staging:bool=False
    resources:dict=field(default_factory=dict)
    lookup:Callable|None=None
    bootstrap_baseline:dict=field(default_factory=dict)
    inventory:Callable|None=None
    query:Callable|None=None
    persist:Callable=lambda _:None
    last_observation:Observation|None=None
    def __repr__(self): return 'LiveContext(private values in memory)'

def _record(ctx,check,statuses,matches,counts,times):
    result=Observation(check,statuses,matches,counts,times)
    ctx.last_observation=result; ctx.persist(result)
    return result

def owner(ctx:LiveContext)->Observation:
    statuses={}; matches=dict.fromkeys(REQUIRED_MATCHES['W1'],False); counts={'gateway_calls':0,'owner_metric_rows':0}; times={'started_at':stamp(ctx.now())}
    save=lambda:_record(ctx,'W1',statuses,matches,counts,times)
    save()
    try:
        snapshot=ctx.main.session(); statuses['session_initial']=snapshot.http_status
        matches['token_usable']=snapshot.usable and snapshot.expires_at_unix_seconds is not None and snapshot.expires_at_unix_seconds>ctx.now().timestamp()
        if not matches['token_usable']: return save()
        start=ctx.now(); counts['gateway_calls']=1; save()
        answer=ctx.main.ask(QUESTION); statuses['ask']=answer.status
        matches['ask_succeeded']=answer.status==200; matches['answer_nonempty']=answer.answer_nonempty
        end=max(ctx.now(),start+timedelta(microseconds=1)); deadline=ctx.now()+timedelta(minutes=15)
        if not matches['ask_succeeded'] or not matches['answer_nonempty']: return save()
        for attempt in range(31):
            rows=ctx.query(start,end) if ctx.query else query_window(ctx.http,ctx.app_id,ctx.metric_token(),start,end)
            matches['owner_metric_match'],counts['owner_metric_rows']=owner_matches(rows,ctx.owner_oid,start,end)
            save()
            if matches['owner_metric_match'] or ctx.now()>=deadline: break
            ctx.progress(); ctx.sleep(min(30,(deadline-ctx.now()).total_seconds()))
    except KeyboardInterrupt: pass
    finally: times['ended_at']=stamp(ctx.now())
    return save()

def refresh(ctx:LiveContext)->Observation:
    statuses={}; matches=dict.fromkeys(REQUIRED_MATCHES['W3'],False); counts={'gateway_calls':0}; times={'started_at':stamp(ctx.now())}
    save=lambda:_record(ctx,'W3',statuses,matches,counts,times)
    save()
    try:
        initial=ctx.main.session(); statuses['session_initial']=initial.http_status
        expiry=initial.expires_at_unix_seconds
        if expiry is None or not initial.token_parseable or not initial.owner_matches or not initial.audience_matches or expiry-ctx.now().timestamp()>14400: return save()
        times['initial_expiry']=stamp(datetime.fromtimestamp(expiry,timezone.utc)); save()
        while ctx.now().timestamp()<expiry:
            ctx.progress(); ctx.sleep(min(30,expiry-ctx.now().timestamp())); save()
        matches['initial_lifetime_elapsed']=True; save()
        statuses['refresh']=ctx.main.refresh(); save()
        refreshed=ctx.main.session(); statuses['session_refreshed']=refreshed.http_status
        later=refreshed.expires_at_unix_seconds
        matches['expiry_advanced']=later is not None and later>expiry and later>ctx.now().timestamp()
        matches['owner_audience_match']=refreshed.usable and refreshed.owner_matches and refreshed.audience_matches
        if later is not None: times['refreshed_expiry']=stamp(datetime.fromtimestamp(later,timezone.utc))
        if statuses['refresh'] not in {200,204} or not matches['expiry_advanced'] or not matches['owner_audience_match']: return save()
        counts['gateway_calls']=1; save(); answer=ctx.main.ask(QUESTION)
        statuses['ask']=answer.status; matches['ask_succeeded']=answer.status==200; matches['answer_nonempty']=answer.answer_nonempty
    except KeyboardInterrupt: pass
    finally: times['ended_at']=stamp(ctx.now())
    return save()

def deployment_access(main,bearer:str)->Observation:
    health=main.health(bearer)
    statuses={'deployment_health':health.http_status}
    for path,key in [('/','deployment_index'),('/ask','deployment_ask'),('/session','deployment_session')]: statuses[key]=main.probe(path,bearer).status
    return Observation('W2',statuses,{'deployment_health_only':health.http_status==200 and health.status=='ok' and health.configuration_valid and health.caller=='deployment' and all(statuses[key]==403 for key in ('deployment_index','deployment_ask','deployment_session'))},{'gateway_calls':0})

def access(ctx:LiveContext)->Observation:
    statuses={}; matches=dict.fromkeys(REQUIRED_MATCHES['W2'],False); counts={'gateway_calls':0,'requests':0}
    times={'started_at':stamp(ctx.now())}; save=lambda:_record(ctx,'W2',statuses,matches,counts,times)
    save(); forged_ok=True
    forged=base64.b64encode(json.dumps({'auth_typ':'aad','claims':[{'typ':'oid','val':ctx.owner_oid},{'typ':'roles','val':'WebApp.Use'}]}).encode()).decode()
    for slot,client in [('main',ctx.anonymous_main),('staging',ctx.anonymous_staging)]:
        refused=True
        for path in ('/health','/','/ask'):
            for headers in (None,{'X-MS-CLIENT-PRINCIPAL':forged,'X-MS-CLIENT-PRINCIPAL-ID':ctx.owner_oid}):
                reply=client.probe(path,None,forged=headers); counts['requests']+=1
                okay=reply.status==401 or reply.status==302 and reply.signin_redirect
                statuses['forged_headers' if headers else 'anonymous_'+slot]=reply.status
                if headers: forged_ok &= okay
                else: refused &= okay
                save()
        matches['anonymous_'+slot+'_refused']=refused
    matches['forged_headers_refused']=forged_ok
    deployment=deployment_access(ctx.main,ctx.deployment_bearer()) if ctx.deployment_bearer else ctx.deployment_observation
    if deployment and deployment.check=='W2':
        statuses.update(deployment.statuses); matches['deployment_health_only']=deployment.matches.get('deployment_health_only') is True
    matches['offline_edges_passed']=ctx.offline_edges_passed is True
    times['ended_at']=stamp(ctx.now()); return save()

def _run(run,repository,sha,kind,conclusion):
    try:
        return run['repository']['full_name']==repository and run['head_repository']['full_name']==repository and run['head_sha']==sha and run['head_branch']=='main' and run['event']=='workflow_dispatch' and run['path']==WORKFLOW and run['status']=='completed' and run['conclusion']==conclusion and run['display_title']==f"webapp-{kind}-{'fixture' if kind=='stage' and conclusion=='failure' else 'candidate'}-{sha}-{run['run_attempt']}"
    except (KeyError,TypeError): return False

def release(receipt:dict,source_metadata:dict)->Observation:
    """Combine captured workflow facts with independently fetched run/job outcomes."""
    repo=source_metadata['repository']; sha=source_metadata['commit']
    from . import evidence
    stage=source_metadata['stage']; promotion=source_metadata['promotion']
    negative=stage.get('conclusion')=='failure'
    observed=evidence.observation(source_metadata['observation'],stage if negative else promotion,repo,mode='stage' if negative else 'promote',fixture=negative,source_run=None if negative else stage)
    if observed.check=='W4':
        valid=validate_source(source_metadata['stage'],receipt,repo,source_metadata['ci']) and _run(source_metadata['promotion'],repo,sha,'promote','success')
        jobs=[job for job in source_metadata['promotion']['jobs'] if job.get('name')=='release']
        valid &= len(jobs)==1 and jobs[0].get('conclusion')=='success' and jobs[0].get('status')=='completed'
        return Observation('W4',observed.statuses,{key:valid and value for key,value in observed.matches.items()},observed.counts,observed.times)
    if observed.check!='W5': raise TransportRefused('invalid_response')
    fixture=source_metadata['stage']; rejected=source_metadata['promotion']
    failed=_run(fixture,repo,sha,'stage','failure') and any(job.get('name')=='release' and job.get('conclusion')=='failure' for job in fixture['jobs'])
    refused=_run(rejected,repo,sha,'promote','failure') and any(job.get('name')=='preflight' and job.get('conclusion')=='failure' for job in rejected['jobs']) and any(job.get('name')=='release' and job.get('conclusion')=='skipped' for job in rejected['jobs'])
    refused=refused and evidence.rejection(source_metadata.get('rejection'),rejected,fixture,repo)
    matches=dict(observed.matches); matches['stage_failed']=failed; matches['promotion_rejected']=refused
    matches['no_swap']=refused and observed.counts.get('swap_calls')==0
    return Observation('W5',observed.statuses,matches,observed.counts,observed.times)

def settings(ctx:LiveContext,promotion_settings:Observation)->Observation:
    matches={'intended_settings_before':promotion_settings.check=='W6a' and promotion_settings.matches.get('intended_settings_before') is True,
             'settings_retained':promotion_settings.matches.get('settings_retained') is True and ctx.configuration()==ctx.intended_configuration,
             'main_owner_signin':False,'staging_owner_signin':False}
    for slot,client in [('main',ctx.main),('staging',ctx.staging)]:
        if getattr(ctx,'fresh_'+slot):
            snapshot=client.session(); health=client.health(None)
            matches[slot+'_owner_signin']=snapshot.usable and snapshot.owner_matches and snapshot.audience_matches and health.http_status==200 and health.caller=='owner' and health.slot==slot and health.configuration_valid
    return _record(ctx,'W6a',{},matches,{'gateway_calls':0},{'measured_at':stamp(ctx.now())})

def teardown(ctx:LiveContext)->Observation:
    statuses={}
    matches=dict.fromkeys(REQUIRED_MATCHES['W6b'],False)
    save=lambda:_record(ctx,'W6b',{},matches,{}, {'measured_at':stamp(ctx.now())})
    save()
    for key in ('webapp_group','gateway_group','plan','site','slot','deleted_apim','bootstrap'):
        try:
            resource=ctx.resources[key]
            statuses[key]=200 if key=='bootstrap' and isinstance(resource,list) and resource and all(ctx.lookup(item)==200 for item in resource) else None if key=='bootstrap' and isinstance(resource,list) else ctx.lookup(resource)
        except TransportRefused: statuses[key]=None
        if key in ('webapp_group','gateway_group','deleted_apim'): matches[key+'_absent']=statuses[key]==404
        elif key=='bootstrap':
            groups=resource if isinstance(resource,list) else [resource]
            matches['bootstrap_intact']=statuses[key]==200 and ctx.inventory is not None and all(ctx.bootstrap_baseline.get(group) and set(ctx.inventory(group))==set(ctx.bootstrap_baseline[group]) for group in groups)
        matches['plan_site_slot_absent']=all(statuses.get(name)==404 for name in ('plan','site','slot'))
        save()
    return save()
