from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import pytest
import httpx
from app.webapp import observations as obs
from app.webapp.contracts import SessionSnapshot,AskObservation,Observation,Health,Probe
from app.webapp.http_client import WebAppClient
from app.webapp.private_az import TransportRefused
from .test_http_client import Unreadable

class Clock:
    def __init__(self): self.value=datetime(2026,10,12,1,tzinfo=timezone.utc); self.waits=[]
    def now(self): return self.value
    def sleep(self,seconds): self.waits.append(seconds); self.value+=timedelta(seconds=seconds)

def context(**kw):
    clock=Clock(); calls=[]
    expiry=int(clock.now().timestamp())+61
    snaps=iter([SessionSnapshot(True,True,True,True,True,True,True,True,expiry,200),SessionSnapshot(True,True,True,True,True,True,True,True,expiry+3600,200)])
    main=SimpleNamespace(session=lambda:next(snaps),refresh=lambda:204,ask=lambda q:(calls.append(q) or AskObservation(200,True)))
    ctx=obs.LiveContext(main=main,staging=main,az=SimpleNamespace(call=lambda _:{}),owner_oid='owner-private',app_id='app-private',metric_token=lambda:'private',http=None,now=clock.now,sleep=clock.sleep,progress=lambda:None)
    for name,value in kw.items(): setattr(ctx,name,value)
    return ctx,clock,calls

def test_owner_waits_at_most_fifteen_minutes_and_never_repeats_question():
    ctx,clock,calls=context()
    ctx.query=lambda start,end:[]
    result=obs.owner(ctx)
    assert result.matches['owner_metric_match'] is False
    assert len(calls)==1 and result.counts['gateway_calls']==1
    assert (clock.now()-datetime(2026,10,12,1,tzinfo=timezone.utc)).total_seconds()==900
    assert all(0<wait<=30 for wait in clock.waits)

def test_owner_matching_metric_completes_without_extra_ask():
    ctx,clock,calls=context()
    ctx.query=lambda start,end:[{'timestamp':start.isoformat(),'Caller':'owner-private','total':14}]
    result=obs.owner(ctx)
    assert all(result.matches.values()) and result.counts['owner_metric_rows']==1 and len(calls)==1 and clock.waits==[]

def test_fresh_owner_sessions_on_both_slots_complete_settings_without_model_calls():
    ctx,_,calls=context(); ctx.fresh_main=ctx.fresh_staging=True
    ctx.configuration=lambda:{'intended':True}; ctx.intended_configuration={'intended':True}
    snap=SessionSnapshot(True,True,True,True,True,True,True,True,1791766900,200)
    ctx.main=SimpleNamespace(session=lambda:snap,health=lambda _:Health(200,'ok','1.0.0','main',True,'owner'))
    ctx.staging=SimpleNamespace(session=lambda:snap,health=lambda _:Health(200,'ok','1.0.0','staging',True,'owner'))
    assert all(obs.settings(ctx,Observation('W6a',matches={'intended_settings_before':True,'settings_retained':True})).matches.values())
    assert calls==[]

def test_refresh_waits_until_initial_expiry_then_requires_later_matching_token():
    ctx,clock,calls=context()
    result=obs.refresh(ctx)
    assert all(result.matches.values())
    assert len(calls)==1 and sum(clock.waits)==61
    assert all(wait<=30 for wait in clock.waits)

def test_refresh_status_without_later_expiry_cannot_call_model():
    ctx,clock,calls=context()
    expiry=int(clock.now().timestamp())+1
    ctx.main.session=lambda:SessionSnapshot(True,True,True,True,True,True,True,True,expiry,200)
    result=obs.refresh(ctx)
    assert result.matches['expiry_advanced'] is False and calls==[]

def test_interrupt_records_incomplete_refresh_and_no_question():
    ctx,clock,calls=context()
    ctx.sleep=lambda _:(_ for _ in ()).throw(KeyboardInterrupt())
    result=obs.refresh(ctx)
    assert not result.matches['initial_lifetime_elapsed'] and calls==[]

@pytest.mark.parametrize('location,want',[('/.auth/login/aad',True),('https://main.example.test/.auth/login/aad?post_login_redirect_uri=/',True),('https://other.example.test/.auth/login/aad',False),('/unrelated',False)])
def test_probe_accepts_only_own_easy_auth_redirect_without_follow_or_body_read(location,want):
    body=Unreadable(); calls=[]
    def handle(request): calls.append(request); return httpx.Response(302,headers={'location':location},stream=body)
    with httpx.Client(transport=httpx.MockTransport(handle),follow_redirects=True) as http:
        result=WebAppClient(http,'https://main.example.test').probe('/',None)
    assert result.signin_redirect is want and result.status==302
    assert len(calls)==1 and body.reads==0

def test_absence_requires_explicit_404_and_bootstrap_requires_200():
    ctx,_,_=context(); ctx.resources={key:key for key in ['webapp_group','gateway_group','plan','site','slot','deleted_apim','bootstrap']}
    ctx.bootstrap_baseline={'bootstrap':['private-resource']}; ctx.inventory=lambda _:['private-resource']
    ctx.lookup=lambda resource:200 if resource=='bootstrap' else 404
    assert all(obs.teardown(ctx).matches.values())
    ctx.lookup=lambda resource:200 if resource=='bootstrap' else 403
    assert not any(value for key,value in obs.teardown(ctx).matches.items() if key!='bootstrap_intact')

def test_bootstrap_group_presence_does_not_prove_missing_resources_intact():
    ctx,_,_=context(); ctx.resources={key:key for key in ['webapp_group','gateway_group','plan','site','slot','deleted_apim','bootstrap']}
    ctx.bootstrap_baseline={'bootstrap':['private-resource']}; ctx.inventory=lambda _:[]
    ctx.lookup=lambda key:200 if key=='bootstrap' else 404
    assert not obs.teardown(ctx).matches['bootstrap_intact']

def test_old_cookie_cannot_claim_fresh_signin_without_attestation():
    ctx,_,_=context(); ctx.fresh_main=False; ctx.fresh_staging=False
    ctx.configuration=lambda: {'intended':True}; ctx.intended_configuration={'intended':True}
    result=obs.settings(ctx,Observation('W6a',matches={'intended_settings_before':True,'settings_retained':True}))
    assert not result.matches['main_owner_signin'] and not result.matches['staging_owner_signin']

def test_deployment_health_does_not_grant_owner_routes():
    class Client:
        def health(self,token): return Health(200,'ok','1.0.0','main',True,'deployment')
        def probe(self,path,token): return Probe(200 if path=='/ask' else 403,False)
    assert not obs.deployment_access(Client(),'private').matches['deployment_health_only']

def test_anonymous_redirect_to_another_origin_fails_access():
    ctx,_,_=context(); routes=[]
    class Anonymous:
        def probe(self,path,token,forged=None): routes.append((path,forged is not None)); return Probe(302,path!='/ask')
    ctx.anonymous_main=ctx.anonymous_staging=Anonymous()
    ctx.offline_edges_passed=True
    ctx.deployment_observation=Observation('W2',matches={'deployment_health_only':True})
    result=obs.access(ctx)
    assert not result.matches['anonymous_main_refused'] and not result.matches['forged_headers_refused']
    assert len(routes)==12 and result.counts['gateway_calls']==0

def test_negative_release_requires_actual_rejected_promotion_not_just_fixture_failure():
    from .test_release import source,REPO,SHA
    stage=source(); stage.update(conclusion='failure',display_title=f'webapp-stage-fixture-{SHA}-1',jobs=[{'name':'release','conclusion':'failure'}])
    promotion=source(); promotion.update(conclusion='failure',display_title=f'webapp-promote-candidate-{SHA}-1',jobs=[{'name':'preflight','conclusion':'failure'},{'name':'release','conclusion':'skipped'}])
    measured=Observation('W5',matches={'fixture_health_503':True,'stage_failed':True,'no_swap':True,'main_unchanged':True},counts={'swap_calls':0})
    metadata=dict(repository=REPO,commit=SHA,stage=stage,promotion=promotion,observation=measured.to_dict())
    assert all(obs.release({},metadata).matches.values())
    promotion['jobs']=[{'name':'release','conclusion':'success'}]
    assert not obs.release({},metadata).matches['promotion_rejected']

def test_empty_bootstrap_resource_list_never_proves_intact():
    ctx,_,_=context(); ctx.resources={key:key for key in ['webapp_group','gateway_group','plan','site','slot','deleted_apim']}; ctx.resources['bootstrap']=[]
    ctx.lookup=lambda _:404
    assert not obs.teardown(ctx).matches['bootstrap_intact']
