"""Public projection rejects missing provenance and preserves late physical cleanup."""
import importlib
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def evidence(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    return importlib.import_module("scripts.pipelines.evidence")


@pytest.mark.parametrize("audience,accepted",[
    ("fb60f99c-7a34-4190-8149-302f77469936",True),
    ("api://AzureADTokenExchange",False),
    ("499b84ac-1321-427f-aa17-267ca6975798",False),
])
def test_regrade_uses_entra_token_audience_at_receipt_time(evidence,audience,accepted):
    # This original is intentionally historical: expiry is checked at collection time.
    expected={"subscription_id":"sub","tenant_id":"tenant","client_id":"client","object_id":"object",
              "issuer":"https://login.microsoftonline.com/tenant/v2.0","subject":"exact-subject"}
    receipt={"source_sha":"a"*40,"release_digest":"b"*64,"at":"2026-01-01T00:00:00+00:00",
             "scheme":"WorkloadIdentityFederation","role":"test",
             "account":{"id":"sub","tenantId":"tenant","user":{"type":"servicePrincipal","name":"client"}},
             "claims":{"federation":{"iss":expected["issuer"],"sub":"exact-subject","aud":audience,"exp":1767229200},
                       "access":{"oid":"object","tid":"tenant","appid":"client","exp":1767229200}}}
    originals=SimpleNamespace(a=SimpleNamespace(source_sha="a"*40,release_digest="b"*64),
                              raw={"principals":{"test":expected}},json=lambda name: receipt)
    if accepted:
        assert evidence._check_identity(originals,"test","test") is None
    else:
        with pytest.raises(evidence.DeliveryRefused,match="identity"):
            evidence._check_identity(originals,"test","test")


def test_provenance_and_timing_grades(evidence,tmp_path):
    result = evidence.grade(tmp_path)
    assert set(result) == {"P8-"+str(i) for i in range(1,9)}
    assert all(v["status"] == "NOT RUN" for v in result.values())
    assert not evidence.timing_pass({"cleanup_started_at":"2026-10-16T09:21:00+00:00",
        "verified_at":"2026-10-16T10:01:00+00:00"},
        {"cleanup_start_by":"2026-10-16T09:20:00+00:00","closeout_by":"2026-10-16T10:00:00+00:00"})
    projection = evidence.public_projection({"source_sha":"a"*40,"release_digest":"b"*64,
        "session":"p8-1016x","token":"NEVER-PUBLIC","tenant_id":"PRIVATE","cost_actual":None},result)
    text = json.dumps(projection)
    assert "NEVER-PUBLIC" not in text and "PRIVATE" not in text
    assert projection["cost_actual"] is None and projection["source_sha"] == "a"*40


def test_public_projection_validates_values_not_just_key_names(evidence):
    projection=evidence.public_projection({"session":"PRIVATE COOKIE","source_sha":"PRIVATE TOKEN"},
        {"P8-1":{"status":"PRIVATE","code":"PRIVATE","correct":"PRIVATE"}})
    assert "PRIVATE" not in json.dumps(projection)


def test_canonical_absence_requires_every_exact_group(evidence):
    rows=[{"id":"group-1","status":404},{"id":"group-2","status":404}]
    evidence.require_group_absence(rows,["group-1","group-2"])
    for bad in (rows[:1],rows+[rows[0]],[rows[0],dict(rows[1],status=403)]):
        with pytest.raises(Exception): evidence.require_group_absence(bad,["group-1","group-2"])


def test_empty_state_requires_successful_original_command(evidence):
    command={"pid":123,"args":["terraform","state","list"],"cwd":"exact"}
    result={"pid":123,"returncode":0,"stdout":"","stderr":""}
    evidence.require_empty_state(command,result,"exact")
    for bad in (dict(result,returncode=1),dict(result,stdout="owned.resource"),dict(result,pid=456)):
        with pytest.raises(Exception): evidence.require_empty_state(command,bad,"exact")


def test_approved_check_from_an_old_plan_is_not_current_approval(evidence):
    check={"id":7,"resource":{"id":"2","type":"environment"},"type_id":"approval-type"}
    suites=[{"context":{"PlanId":"current"},"checkRuns":[{"id":"run","status":"approved",
        "checkConfigurationRef":{"id":7,"resource":check["resource"],"type":{"id":"approval-type"}}}]}]
    evidence.require_evaluations(suites,[check],"current")
    with pytest.raises(Exception): evidence.require_evaluations(suites,[check],"different-plan")


def test_negative_regrade_rejects_incorrect_nonempty_matches(evidence):
    from types import SimpleNamespace
    rows=[SimpleNamespace(hits=[]) for _ in range(229)]
    evidence.require_no_matches(rows)
    rows[0].hits=[SimpleNamespace(artefact="incorrect")]
    with pytest.raises(Exception): evidence.require_no_matches(rows)


def test_container_receipt_matches_the_actual_published_image(evidence):
    binding={"source_sha":"a"*40,"config_digest":"sha256:"+"b"*64,"digest":"sha256:"+"c"*64}
    receipt={"kind":"container-toolchain","source_sha":"a"*40,"config_digest":binding["config_digest"],
        "versions":{"terraform":"1.15.8","azure_cli":"2.81.0","agent":"5.280.0","python":"3.12"}}
    log="tests passed\nP8_TOOLCHAIN_RECEIPT="+json.dumps(receipt)+"\n"
    evidence.require_toolchain(log,binding,"a"*40)
    with pytest.raises(Exception): evidence.require_toolchain(log,dict(binding,config_digest="sha256:"+"d"*64),"a"*40)
    with pytest.raises(Exception): evidence.require_toolchain("Terraform v1.15.8 2.81.0 5.280.0",binding,"a"*40)


def test_cleanup_can_regrade_authenticated_never_created_state(evidence):
    raw={"storage_host":"owned.blob.core.windows.net","search_host":"owned.search.windows.net"}
    component={"name":"index-test"}
    proof={"component":"index-test","backend":{"kind":"blob","host":raw["storage_host"],
        "container":"test-state","key":"index.tfstate","status":404,"error_code":"BlobNotFound","length":0},
        "index":{"kind":"index","host":raw["search_host"],"name":"p8-test","status":404}}
    evidence.require_index_absence(proof,component,raw,{"components":[]})
    proof["index"]["status"]=200
    with pytest.raises(Exception): evidence.require_index_absence(proof,component,raw,{"components":[]})
