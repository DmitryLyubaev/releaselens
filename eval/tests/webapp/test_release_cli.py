import json
import httpx
import pytest
from app.gateway.freeze import DirtyTreeError
from app.webapp import release_cli as cli
from .test_release import source, ci, SHA, REPO
from .test_release import receipt


def test_release_guards_run_before_reading_credentials_or_opening_http(capsys):
    class Secrets:
        def __getitem__(self, key): raise AssertionError("Credentials read before guard")
        def get(self, key, default=None): raise AssertionError("Credentials read before guard")
    def refuse(): raise DirtyTreeError("uncommitted changes")
    assert cli.main(["stage"], env=Secrets(), clean_guard=refuse) == 1
    assert capsys.readouterr().out == "status=refused\n"


def test_source_api_projects_only_public_release_metadata():
    raw = source() | {"actor": {"email": "private-marker@example.test"}, "runtime": "private-marker"}
    def handle(request):
        if request.url.path.endswith("/jobs"): return httpx.Response(200, json={"jobs": raw["jobs"]})
        return httpx.Response(200, json=raw)
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = cli.GitHubMetadata(http, REPO).source(123)
    assert "private-marker" not in json.dumps(result)
    assert result["head_sha"] == SHA


def test_preflight_ci_requires_the_actual_build_job_to_pass():
    raw = ci() | {"id": 456, "run_attempt": 1}
    def handle(request):
        if request.url.path.endswith("/jobs"): return httpx.Response(200, json={"jobs": [{"name": "build-and-test", "status": "completed", "conclusion": "failure"}]})
        return httpx.Response(200, json={"workflow_runs": [raw]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(cli.TransportRefused): cli.GitHubMetadata(http, REPO).ci(SHA)


@pytest.mark.parametrize("mode,fixture", [("seed", "true"), ("promote", "true"), ("unknown", "false")])
def test_invalid_mode_fixture_combination_refuses_without_azure(mode, fixture, capsys, tmp_path):
    env = {"GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch", "WEBAPP_MODE": mode, "WEBAPP_FIXTURE": fixture}
    assert cli.main(["preflight"], env=env, clean_guard=lambda: None, output_guard=lambda _: None, root=tmp_path) == 1
    assert capsys.readouterr().out == "status=refused\n"

def test_fixture_ineligibility_has_exact_selected_run_rejection_record(tmp_path,capsys):
    fixture=source(); fixture.update(conclusion='failure',display_title=f'webapp-stage-fixture-{SHA}-1',jobs=[{'name':'release','conclusion':'failure','status':'completed'}])
    class API:
        def __init__(self,*args): pass
        def ci(self,sha): return ci()
        def source(self,run): return fixture
    env=dict(GITHUB_ACTIONS='true',GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='workflow_dispatch',WEBAPP_MODE='promote',WEBAPP_FIXTURE='false',GITHUB_REPOSITORY=REPO,GITHUB_SHA=SHA,GITHUB_RUN_ID='900',GITHUB_RUN_ATTEMPT='1',SOURCE_RUN_ID='123',SOURCE_RUN_ATTEMPT='1')
    from unittest.mock import patch
    with patch.object(cli,'GitHubMetadata',API):
        assert cli.main(['preflight'],env=env,clean_guard=lambda:None,output_guard=lambda _:None,root=tmp_path)==1
    record=json.loads((tmp_path/'artifacts/webapp-release/rejection.json').read_text())
    assert record['reason']=='fixture_ineligible' and record['source_run_id']==123 and record['source_run_attempt']==1 and record['source_sha_selected']==SHA


def test_invalid_promotion_receipt_is_rejected_before_azure_credential_acquisition(tmp_path, capsys):
    import hashlib, io, zipfile
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        for name in ("ReleaseLens.Web.dll", "ReleaseLens.Web.deps.json", "ReleaseLens.Web.runtimeconfig.json"):
            archive.writestr(name, "content")
    value = package.getvalue(); digest = hashlib.sha256(value).hexdigest()
    bundle = tmp_path / "artifacts/webapp-verified"; bundle.mkdir(parents=True)
    (bundle / "package.zip").write_bytes(value)
    (bundle / "receipt.json").write_text(json.dumps(receipt() | {"package_sha256": digest, "kind": "fixture"}))
    (bundle / "verified.json").write_text(json.dumps(dict(current_sha=SHA, repository=REPO, mode="promote", fixture=False,
                                                              execution_ci=ci(), ci=ci(), source=source(), package_sha256=digest)))
    env = dict(GITHUB_ACTIONS="true", GITHUB_REF="refs/heads/main", GITHUB_EVENT_NAME="workflow_dispatch", WEBAPP_MODE="promote", WEBAPP_FIXTURE="false",
               GITHUB_REPOSITORY=REPO, GITHUB_SHA=SHA, GITHUB_RUN_ID="900", GITHUB_RUN_ATTEMPT="1", AZURE_TENANT_ID="placeholder", AZURE_CLIENT_ID="placeholder")
    acquisitions = []
    def credential(*args): acquisitions.append(True); raise AssertionError("Credential should not be acquired")
    assert cli.main(["promote"], env=env, clean_guard=lambda: None, output_guard=lambda _: None, root=tmp_path, credential_factory=credential) == 1
    assert acquisitions == []
    assert capsys.readouterr().out == "status=refused\n"

def test_seed_receipt_is_not_eligible_and_records_deployment_owner_route_boundaries(tmp_path,capsys):
    import hashlib,io,zipfile
    package=io.BytesIO()
    with zipfile.ZipFile(package,'w') as archive:
        for name in ('ReleaseLens.Web.dll','ReleaseLens.Web.deps.json','ReleaseLens.Web.runtimeconfig.json'): archive.writestr(name,'content')
    value=package.getvalue(); digest=hashlib.sha256(value).hexdigest()
    bundle=tmp_path/'artifacts/webapp-verified'; bundle.mkdir(parents=True)
    (bundle/'package.zip').write_bytes(value)
    (bundle/'verified.json').write_text(json.dumps(dict(current_sha=SHA,repository=REPO,mode='seed',fixture=False,execution_ci=ci(),ci=ci(),package_sha256=digest,version=SHA+'.900.1.seed')))
    env=dict(GITHUB_ACTIONS='true',GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='workflow_dispatch',WEBAPP_MODE='seed',WEBAPP_FIXTURE='false',GITHUB_REPOSITORY=REPO,GITHUB_SHA=SHA,GITHUB_RUN_ID='900',GITHUB_RUN_ATTEMPT='1',AZURE_TENANT_ID='placeholder',AZURE_CLIENT_ID='placeholder',AZURE_SUBSCRIPTION_ID='placeholder',WEBAPP_DEPLOY_OBJECT_ID='placeholder',WEBAPP_RESOURCE_ID='placeholder',WEBAPP_FRONTEND_AUDIENCE='api://placeholder',WEBAPP_MAIN_URL='https://main.example.test',WEBAPP_STAGING_URL='https://stage.example.test',WEBAPP_INTENDED_CONFIG='{}')
    class Cloud:
        def __init__(self,*args): pass
        def configuration(self,_): return {}
        def selected_package_present(self,_): return False
        def deploy(self,*args): pass
        def selected_package_sha256(self,_): return digest
    class Credential:
        def __init__(self,*args): pass
        def get_token(self,_): return type('Token',(),{'token':'private'})()
    def handle(request):
        if request.url.path=='/health': return httpx.Response(200,json=dict(status='ok',version=SHA+'.900.1.seed',slot='main',configurationValid=True,caller='deployment'))
        return httpx.Response(403)
    from unittest.mock import patch
    with patch.object(cli,'AzureReleaseTransport',Cloud),httpx.Client(transport=httpx.MockTransport(handle)) as http:
        assert cli.main(['seed'],env=env,http=http,clean_guard=lambda:None,output_guard=lambda _:None,root=tmp_path,credential_factory=Credential)==0
    result=json.loads((tmp_path/'artifacts/webapp-release/observation-W2.json').read_text())
    assert result['run_id']==900 and result['run_attempt']==1 and result['source_sha']==SHA and result['repository']==REPO
    assert result['observation']['matches']['deployment_health_only'] is True
    assert json.loads((tmp_path/'artifacts/webapp-release/receipt.json').read_text())['kind']=='seed'


def publish_with_previous_worker(tmp_path,monkeypatch,mode,*,digest_matches=True,settles=True,previous_caller='deployment',package_present=True):
    import hashlib,io,zipfile
    from unittest.mock import patch
    package=io.BytesIO()
    with zipfile.ZipFile(package,'w') as archive:
        for name in ('ReleaseLens.Web.dll','ReleaseLens.Web.deps.json','ReleaseLens.Web.runtimeconfig.json'):archive.writestr(name,'content')
    value=package.getvalue();digest=hashlib.sha256(value).hexdigest()
    version=f'{SHA}.900.1.{mode}';old_version=f'{SHA}.777.1.seed';baseline=f'{SHA}.122.1.seed'
    bundle=tmp_path/'artifacts/webapp-verified';bundle.mkdir(parents=True)
    (bundle/'package.zip').write_bytes(value)
    (bundle/'verified.json').write_text(json.dumps(dict(current_sha=SHA,repository=REPO,mode=mode,fixture=False,execution_ci=ci(),ci=ci(),package_sha256=digest,version=version)))
    env=dict(GITHUB_ACTIONS='true',GITHUB_REF='refs/heads/main',GITHUB_EVENT_NAME='workflow_dispatch',WEBAPP_MODE=mode,WEBAPP_FIXTURE='false',GITHUB_REPOSITORY=REPO,GITHUB_SHA=SHA,GITHUB_RUN_ID='900',GITHUB_RUN_ATTEMPT='1',AZURE_TENANT_ID='placeholder',AZURE_CLIENT_ID='placeholder',AZURE_SUBSCRIPTION_ID='placeholder',WEBAPP_DEPLOY_OBJECT_ID='placeholder',WEBAPP_RESOURCE_ID='placeholder',WEBAPP_FRONTEND_AUDIENCE='api://placeholder',WEBAPP_MAIN_URL='https://main.example.test',WEBAPP_STAGING_URL='https://stage.example.test',WEBAPP_INTENDED_CONFIG='{}',WEBAPP_BASELINE_VERSION=baseline)
    state=dict(deployments=0,after_health=0,digests=0,events=[])
    class Cloud:
        def __init__(self,*args):pass
        def configuration(self,_):return {}
        def selected_package_present(self,_):
            state['events'].append('package_presence')
            return package_present
        def deploy(self,*args):state['deployments']+=1;state['events'].append('upload')
        def selected_package_sha256(self,_):
            state['digests']+=1;state['events'].append('digest')
            return digest if digest_matches and not (digest_matches=='changes' and state['digests']>1) else 'c'*64
    class Credential:
        def __init__(self,*args):pass
        def get_token(self,_):return type('Token',(),{'token':'private'})()
    target='main' if mode=='seed' else 'staging'
    def handle(request):
        if request.url.path!='/health':return httpx.Response(403)
        slot='main' if request.url.host=='main.example.test' else 'staging'
        if slot!=target:return httpx.Response(200,json=dict(status='ok',version=baseline,slot=slot,configurationValid=True,caller='deployment'))
        if state['deployments']:
            state['after_health']+=1;state['events'].append('health_after')
            observed=version if package_present is False or settles and state['after_health']>=2 else old_version
        else:
            if package_present is False: raise httpx.ReadTimeout('Empty slot has no app worker',request=request)
            state['events'].append('health_before');observed=old_version
        return httpx.Response(200,json=dict(status='ok',version=observed,slot=slot,configurationValid=True,caller=previous_caller))
    monkeypatch.setattr(cli.time,'sleep',lambda _:None)
    with patch.object(cli,'AzureReleaseTransport',Cloud),httpx.Client(transport=httpx.MockTransport(handle)) as http:
        code=cli.main([mode],env=env,http=http,clean_guard=lambda:None,output_guard=lambda _:None,root=tmp_path,credential_factory=Credential)
    return code,state


@pytest.mark.parametrize('mode',['seed','stage'])
def test_publish_waits_for_previous_verified_worker_to_yield_to_new_package(tmp_path,monkeypatch,mode):
    code,state=publish_with_previous_worker(tmp_path,monkeypatch,mode)
    assert code==0
    assert state['deployments']==1
    assert state['events'].index('health_before')<state['events'].index('upload')
    assert state['events'].index('digest')<state['events'].index('health_after')
    assert json.loads((tmp_path/'artifacts/webapp-release/receipt.json').read_text())['checks_passed'] is True


@pytest.mark.parametrize('mode',['seed','stage'])
def test_selected_package_mismatch_blocks_post_upload_health_and_receipt(tmp_path,monkeypatch,mode):
    code,state=publish_with_previous_worker(tmp_path,monkeypatch,mode,digest_matches=False)
    assert code==1
    assert state['after_health']==0
    assert not (tmp_path/'artifacts/webapp-release/receipt.json').exists()


@pytest.mark.parametrize('mode',['seed','stage'])
def test_previous_worker_cannot_be_accepted_as_new_release_after_poll_budget(tmp_path,monkeypatch,mode):
    code,state=publish_with_previous_worker(tmp_path,monkeypatch,mode,settles=False)
    assert code==1
    assert state['deployments']==1
    assert state['after_health']==30
    assert not (tmp_path/'artifacts/webapp-release/receipt.json').exists()


@pytest.mark.parametrize('mode',['seed','stage'])
def test_invalid_previous_worker_identity_blocks_upload(tmp_path,monkeypatch,mode):
    code,state=publish_with_previous_worker(tmp_path,monkeypatch,mode,previous_caller='owner')
    assert code==1
    assert state['deployments']==0


@pytest.mark.parametrize('mode',['seed','stage'])
def test_package_selection_must_still_match_after_readiness_polling(tmp_path,monkeypatch,mode):
    code,state=publish_with_previous_worker(tmp_path,monkeypatch,mode,digest_matches='changes')
    assert code==1
    assert state['digests']==2
    assert not (tmp_path/'artifacts/webapp-release/receipt.json').exists()


@pytest.mark.parametrize('mode',['seed','stage'])
def test_first_publish_does_not_require_an_uninstalled_worker(tmp_path,monkeypatch,mode):
    code,state=publish_with_previous_worker(tmp_path,monkeypatch,mode,package_present=False)
    assert code==0
    assert state['deployments']==1
    assert 'health_before' not in state['events']
    assert state['events'].index('package_presence')<state['events'].index('upload')
    assert state['events'].index('digest')<state['events'].index('health_after')
    assert json.loads((tmp_path/'artifacts/webapp-release/receipt.json').read_text())['checks_passed'] is True


@pytest.mark.parametrize('mode',['seed','stage'])
def test_first_publish_still_refuses_wrong_selected_package(tmp_path,monkeypatch,mode):
    code,state=publish_with_previous_worker(tmp_path,monkeypatch,mode,package_present=False,digest_matches=False)
    assert code==1
    assert state['deployments']==1 and state['after_health']==0
    assert not (tmp_path/'artifacts/webapp-release/receipt.json').exists()
