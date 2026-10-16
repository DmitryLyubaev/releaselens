"""The Windows cutoff produces a durable, exact-build owner-browser handoff."""
import importlib
import json
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace

import pytest


@pytest.fixture
def handoff(repo_root,monkeypatch,tmp_path):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    module=importlib.import_module("scripts.pipelines.quiescence")
    start=datetime.now(timezone.utc)-timedelta(minutes=76)
    a=SimpleNamespace(session="p8-1016x",source_sha="a"*40,tree_sha="b"*40,manifest_sha256="c"*64,
        queue_cutoff_at=(start+timedelta(minutes=75)).isoformat())
    raw={"pipeline_id":2}
    monkeypatch.setattr(module,"load",lambda path:(a,raw,{}))
    monkeypatch.setattr(module,"verify_source",lambda a:None)
    (tmp_path/"intents").mkdir()
    (tmp_path/"intents/dispatch-release.json").write_text(json.dumps({"at":start.isoformat()}))
    build={"id":13,"definition":{"id":2},"sourceVersion":a.source_sha,"sourceBranch":"refs/heads/main",
        "reason":"manual","queueTime":(start+timedelta(seconds=10)).isoformat()}
    return module,tmp_path,a,raw,build


def test_cutoff_is_create_only_and_contains_only_registered_owned_builds(handoff):
    module,path,a,raw,build=handoff
    module.register_build(path,"release",build)
    now=datetime.now(timezone.utc)
    request=module.request(path,a,raw,now)
    assert request["pipeline_id"]==2 and request["build_ids"]==[13]
    original=(path/"quiescence/request.json").read_bytes()
    assert module.request(path,a,raw,now+timedelta(minutes=1))==request
    assert (path/"quiescence/request.json").read_bytes()==original
    with pytest.raises(Exception): module.register_build(path,"release",dict(build,id=14))


@pytest.mark.parametrize("change",[{"sourceVersion":"d"*40},{"definition":{"id":1}},{"reason":"schedule"}])
def test_wrong_build_never_enters_cancellation_list(handoff,change):
    module,path,a,raw,build=handoff
    with pytest.raises(Exception): module.register_build(path,"release",build|change)


def test_browser_ack_requires_originals_and_exact_request_digest(handoff,monkeypatch):
    module,path,a,raw,build=handoff
    module.register_build(path,"release",build)
    request=module.request(path,a,raw,datetime.now(timezone.utc))
    with pytest.raises(Exception): module.acknowledge(path,"0"*64,"verified")
    from app.delivery_search.contracts import sha256_file
    digest=sha256_file(path/"quiescence/request.json")
    with pytest.raises(Exception): module.acknowledge(path,digest,"verified")
    module.acknowledge(path,digest,"unavailable")
    status=module.status(path,a,raw)
    assert status["browser_attempt"]=="unavailable" and status["verified"] is False


def test_due_cleanup_records_missing_browser_ack_without_waiting(handoff):
    module,path,a,raw,build=handoff
    module.request(path,a,raw,datetime.now(timezone.utc))
    status=module.status(path,a,raw)
    assert status=={"verified":False,"browser_attempt":"missing","request_sha256":module.sha256_file(path/"quiescence/request.json")}


def test_verified_ack_rechecks_actual_browser_originals(handoff):
    module,path,a,raw,build=handoff
    module.register_build(path,"release",build)
    module.request(path,a,raw,datetime.now(timezone.utc))
    definition={"id":2,"variables":{"original":{"value":"test-only"}},"queueStatus":"enabled"}
    originals={"definition-original":definition,"definition-disabled":definition|{"queueStatus":"disabled"},
        "queue-after":{"count":0,"value":[]},"quiesced-build-13":build|{"status":"completed"}}
    files={}
    for key,value in originals.items():
        original=path/(key+".json"); original.write_text(json.dumps(value))
        files[key]={"kind":"actual","session":a.session,"source_sha":a.source_sha,"channel":"owner-browser",
            "path":original.name,"sha256":module.sha256_file(original),"captured_at":datetime.now(timezone.utc).isoformat()}
    (path/"originals.json").write_text(json.dumps({"kind":"actual","session":a.session,"source_sha":a.source_sha,"files":files}))
    module.acknowledge(path,module.sha256_file(path/"quiescence/request.json"),"verified")
    assert module.status(path,a,raw)["verified"] is True
    (path/"queue-after.json").write_text(json.dumps({"count":1,"value":[build]}))
    with pytest.raises(Exception): module.status(path,a,raw)


def test_watchdog_requests_owner_browser_at_queue_cutoff(handoff,monkeypatch):
    module,path,a,raw,build=handoff
    watchdog=importlib.import_module("scripts.pipelines.watchdog")
    before=datetime.fromisoformat(a.queue_cutoff_at)-timedelta(seconds=1)
    watchdog.cutoff_handoff(path,a,raw,before)
    assert not (path/"quiescence/request.json").exists()
    watchdog.cutoff_handoff(path,a,raw,before+timedelta(seconds=1))
    assert (path/"quiescence/request.json").exists()


def test_verified_ack_accepts_actual_api_omission_of_empty_variables(handoff):
    module,path,a,raw,build=handoff
    module.request(path,a,raw,datetime.now(timezone.utc))
    # Real BuildDefinition responses omit variables when the dictionary is empty.
    definition={"id":2,"queueStatus":"disabled","process":{"yamlFilename":"pipelines/search-delivery.yml"}}
    values={"definition-original":definition,"definition-disabled":dict(definition),"queue-after":{"count":0,"value":[]}}
    files={}
    for key,value in values.items():
        original=path/(key+".json");original.write_text(json.dumps(value))
        files[key]={"kind":"actual","session":a.session,"source_sha":a.source_sha,"channel":"owner-browser",
            "path":original.name,"sha256":module.sha256_file(original),"captured_at":datetime.now(timezone.utc).isoformat()}
    (path/"originals.json").write_text(json.dumps({"kind":"actual","session":a.session,"source_sha":a.source_sha,"files":files}))
    module.acknowledge(path,module.sha256_file(path/"quiescence/request.json"),"verified")
    assert module.status(path,a,raw)["verified"] is True


@pytest.mark.parametrize("before,after",[({},{}),({},{"variables":{}}),({"variables":{}},{})])
def test_restoration_treats_only_an_omitted_empty_variable_dictionary_as_empty(handoff,before,after):
    module,*_=handoff
    definition={"id":2,"queueStatus":"disabled"}
    module.require_restored_definition(definition|before,definition|after,2)


@pytest.mark.parametrize("change",[
    {"variables":{"unexpected":{"value":"test-only"}}},
    {"variables":None},
    {"id":1},
    {"queueStatus":"enabled"},
    {"repository":{"changed":True}},
    {"process":{"yamlFilename":"other.yml"}},
    {"queue":{"id":99}},
    {"triggers":[{"changed":True}]},
    {"options":[{"enabled":True}]},
    {"properties":{"changed":True}},
    {"jobAuthorizationScope":"projectCollection"},
])
def test_restoration_still_rejects_changed_variables_or_protected_configuration(handoff,change):
    module,*_=handoff
    definition={"id":2,"queueStatus":"disabled"}
    with pytest.raises(Exception):module.require_restored_definition(definition,definition|change,2)
