import hashlib,io,json,zipfile
from copy import deepcopy
from datetime import datetime,timezone
import httpx,pytest
from app.webapp import evidence
from app.webapp.private_az import TransportRefused
from app.webapp.contracts import Observation
from app.webapp.release_cli import GitHubMetadata
from .test_release import source,receipt,ci,REPO,SHA

def envelope(check='W4',mode='promote',fixture=False):
    return dict(schema=1,repository=REPO,source_sha=SHA,run_id=124,run_attempt=1,mode=mode,fixture=fixture,
                measured_at='2026-10-12T00:01:00Z',source_run_id=123 if mode=='promote' else None,source_run_attempt=1 if mode=='promote' else None,
                observation=Observation(check).to_dict())

def promotion():
    run=source(); run.update(id=124,display_title=f'webapp-promote-candidate-{SHA}-1')
    return run

@pytest.mark.parametrize('key,bad',[('run_id',125),('run_attempt',2),('source_sha','b'*40),('repository','foreign/project'),('measured_at','2026-01-01T00:01:00Z'),('source_run_id',99),('source_run_attempt',2)])
def test_stale_or_unrelated_envelope_cannot_acquire_current_run_provenance(key,bad):
    value=envelope(); value[key]=bad
    with pytest.raises(TransportRefused): evidence.observation(value,promotion(),REPO,mode='promote',fixture=False,source_run=source())

def test_bare_local_booleans_cannot_become_workflow_evidence():
    with pytest.raises(TransportRefused): evidence.observation(Observation('W4').to_dict(),promotion(),REPO,mode='promote',fixture=False,source_run=source())

def archive(value):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as zipped: zipped.writestr('observation-W4.json',json.dumps(value))
    return data.getvalue()

def test_original_artifact_zip_must_match_github_run_bound_digest(tmp_path):
    value=archive(envelope()); path=tmp_path/'original.zip'; path.write_bytes(value)
    artifact=dict(id=88,name='webapp-observations-124-1',expired=False,created_at='2026-10-12T00:01:20Z',digest='sha256:'+hashlib.sha256(value).hexdigest(),workflow_run=dict(id=124,head_sha=SHA,head_branch='main'))
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'total_count':1,'artifacts':[artifact]}))) as http:
        api=GitHubMetadata(http,REPO)
        result=evidence.artifact(api,promotion(),'webapp-observations-124-1',path)
        assert result['observation-W4.json']['run_id']==124
        path.write_bytes(archive(dict(envelope(),run_id=999)))
        with pytest.raises(TransportRefused): evidence.artifact(api,promotion(),'webapp-observations-124-1',path)

@pytest.mark.parametrize('reason,selected',[('metadata_failure',123),('fixture_ineligible',999)])
def test_unrelated_preflight_failure_never_counts_as_selected_fixture_rejection(reason,selected):
    rejection=dict(schema=1,repository=REPO,source_sha=SHA,run_id=124,run_attempt=1,mode='promote',measured_at='2026-10-12T00:01:00Z',source_run_id=selected,source_run_attempt=1,source_sha_selected=SHA,reason=reason)
    with pytest.raises(TransportRefused): evidence.rejection(rejection,promotion(),source(),REPO)

@pytest.mark.parametrize('bad',['bare','stale','valid'])
def test_release_loader_rejects_old_observations_even_with_current_successful_metadata(tmp_path,bad):
    from app.webapp.__main__ import load_release
    import re
    stage=source(); promoted=promotion()
    value=envelope()
    if bad=='bare': value=Observation('W4').to_dict()
    elif bad=='stale': value['measured_at']='2026-01-01T00:01:00Z'
    else: value['observation']=Observation('W4',matches=dict.fromkeys('baseline_healthy staging_healthy approval_recorded swap_succeeded main_candidate staging_baseline slot_labels_retained'.split(),True)).to_dict()
    artifacts={}
    for run,kind,files in [(stage,'stage',{'receipt.json':receipt()}),(promoted,'observations',{'observation-W4.json':value,'observation-W6a.json':dict(envelope('W6a'))})]:
        name=f"webapp-{kind}-{run['id']}-1"; buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as zipped:
            for filename,payload in files.items(): zipped.writestr(filename,json.dumps(payload))
        data=buffer.getvalue(); (tmp_path/(name+'.zip')).write_bytes(data)
        artifacts[run['id']]=dict(total_count=1,artifacts=[dict(name=name,expired=False,created_at='2026-10-12T00:01:20Z',digest='sha256:'+hashlib.sha256(data).hexdigest(),workflow_run=dict(id=run['id'],head_sha=SHA,head_branch='main'))])
    class API:
        repository=REPO
        def source(self,id): return stage if id==123 else promoted
        def ci(self,sha): return ci()
        def _get(self,path): return artifacts[int(re.search(r'runs/(\d+)',path)[1])]
    if bad=='valid':
        trusted,data,settings=load_release(API(),{'WEBAPP_STAGE_RUN_ID':'123','WEBAPP_PROMOTION_RUN_ID':'124'},SHA,tmp_path)
        assert trusted['run_id']==123 and data['observation']['run_id']==124 and settings.check=='W6a'
    else:
        with pytest.raises(TransportRefused): load_release(API(),{'WEBAPP_STAGE_RUN_ID':'123','WEBAPP_PROMOTION_RUN_ID':'124'},SHA,tmp_path)
