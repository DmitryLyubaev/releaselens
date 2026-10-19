"""One direct owned-index GET per call, within the existing metadata budget."""
import hashlib
import json
import re
from datetime import timezone

def observe_test_index(directory,a,manifest,client,token,reserve,now,*,cleanup):
    from app.delivery_search.contracts import SearchTarget,exclusive_json,DeliveryRefused
    from .metadata import require_policy
    policy=require_policy(a)
    if not isinstance(a.get('session'),str) or not re.fullmatch(r'[a-z][a-z0-9-]{3,11}',a['session']):
        raise ValueError('valid bound session required')
    components=[c for c in manifest['components'] if c['name']=='index-test']
    if len(components)!=1 or len(components[0]['resources'])!=1:raise ValueError('exact index required')
    resource=components[0]['resources'][0]
    if resource.get('kind')!='index' or resource['name']!='p8-test' or resource['host']!=a['search_host']:
        raise ValueError('manifest target mismatch')
    target=SearchTarget('https://'+a['search_host'],'p8-test',a['search_host'])
    binding={k:a[k] for k in ('session','source_sha','manifest_sha256')}|{'host':target.allowed_host,'name':target.index}
    presence=directory/'independent-test-index-present.json'
    previous=json.loads(presence.read_bytes()) if presence.exists() else None
    if previous is not None and (any(previous.get(k)!=v for k,v in binding.items()) or previous['status']!=200):
        raise ValueError('prior presence binding mismatch')
    folder=directory/'physical-index-observations';folder.mkdir(exist_ok=True)
    # Local create-only slots also bound repeated manual observations. Shared Blob
    # reservations remain the authority for the explicitly bound policy.
    started=now() if callable(now) else now
    for i in range(12):
        try:
            exclusive_json(folder/(f'{i:02}-intent.json'),binding|{'at':started.isoformat(),'method':'GET'})
            break
        except DeliveryRefused as exc:
            if exc.code!='reserved':raise
    else:raise ValueError('twelve physical observations consumed')
    claim=reserve()
    if (not isinstance(claim,str) or not re.fullmatch(r'budgets/metadata/[0-9]{3}\.json',claim)
        or not policy.ordinary<=int(claim.rsplit('/',1)[1][:3])<policy.total):
        raise DeliveryRefused('authority')
    response=client.get(target.endpoint+'/indexes/'+target.index,params={'api-version':'2024-07-01'},
                        headers={'Authorization':'Bearer '+token},follow_redirects=False,timeout=15)
    if response.status_code not in (200,404):raise ValueError('physical observation unavailable')
    if response.status_code==200 and response.json().get('name')!=target.index:raise ValueError('index response mismatch')
    # Record after the actual response. Caller supplies a clock callable in live use;
    # tests can supply a fixed datetime without changing network behavior.
    observed=now() if callable(now) else now
    proof=binding|{'at':observed.astimezone(timezone.utc).isoformat(),'method':'GET','status':response.status_code,
        'body_sha256':hashlib.sha256(response.content).hexdigest(),'metadata_budget_claim':claim,
        'cleanup_observation':cleanup,'previously_present':previous is not None,
        'prior_presence_sha256':hashlib.sha256(presence.read_bytes()).hexdigest() if previous is not None else None,
        'first_delete_timestamp_not_inferred':True}
    exclusive_json(folder/(f'{i:02}-response.json'),proof)
    if not cleanup and response.status_code==200 and previous is None:exclusive_json(presence,proof)
    absence=directory/'independent-test-index-first-absence.json'
    if cleanup and response.status_code==404 and previous is not None and not absence.exists():exclusive_json(absence,proof)
    return proof
