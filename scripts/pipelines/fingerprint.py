"""Read complete ordered index fingerprints within the shared metadata budget.

Callers supply reviewed authority, transport, deadlines and create-only evidence paths.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
from types import SimpleNamespace


def sha(data):
    return hashlib.sha256(data).hexdigest()

def fingerprint_capacity(ordinary_used,phase,*,policy=None):
    from .metadata import validate_policy
    approved=validate_policy(policy) if policy is not None else None
    ordinary=approved.ordinary if approved else 108
    readiness=approved.readiness_reads_per_loop if approved else 1
    contingency=approved.cleanup_contingency if approved else 0
    if (phase not in ('before','after') or type(ordinary_used) is not int or ordinary_used<0
            or ordinary_used+(86 if phase=='before' else 43)+(2 if phase=='before' else 1)*readiness+contingency>ordinary):
        raise ValueError('insufficient ordinary metadata capacity')

def page_parameters(page):
    if type(page) is not int or not 0<=page<42:raise ValueError('invalid fingerprint page')
    params={'search':'*','$select':'chunk_id,artefact,content','$top':'1000',
            '$skip':str(page*1000),'$orderby':'chunk_id asc'}
    if page==0:params['$count']='true'
    return params

def verified_documents(pages,expected_ids):
    if len(pages)!=42 or pages[0].get('@odata.count')!=41825 or len(expected_ids)!=41825:
        raise ValueError('fingerprint coverage mismatch')
    docs=[]
    for page,result in enumerate(pages):
        rows=result['value']
        if len(rows)!=(1000 if page<41 else 825):raise ValueError('incomplete fingerprint page')
        docs.extend({k:r[k] for k in ['chunk_id','artefact','content']} for r in rows)
    if pages[-1].get('@odata.nextLink') or pages[-1].get('@search.nextPageParameters'):
        raise ValueError('unexpected final continuation')
    ids=[r['chunk_id'] for r in docs]
    if ids!=sorted(ids) or len(set(ids))!=41825 or set(ids)!=expected_ids:
        raise ValueError('unordered or incomplete fingerprint')
    docs.sort(key=lambda r:int(r['chunk_id']))
    return docs

def fingerprint_get(client,store,tokens,host,suffix,params,folder,label,captured):
    from app.delivery_search.contracts import DeliveryRefused
    if store.metadata_policy is None: raise DeliveryRefused('authority')
    claim=store.reserve_metadata()
    response=client.get('https://'+host+'/indexes/p8-demo-prod'+suffix,
        params={'api-version':'2024-07-01',**params},
        headers={'Authorization':'Bearer '+tokens.token()},timeout=20)
    body=response.content
    with (folder/(label+'.json')).open('xb') as out:out.write(body)
    captured.append({'at':datetime.now(timezone.utc).isoformat(),'method':'GET',
        'url':str(response.request.url),'status':response.status_code,'sha256':sha(body),
        'metadata_budget_claim':claim,'file':label+'.json'})
    if response.status_code!=200:raise ValueError('fingerprint Search read failed')
    return response.json()

def capture_search_originals(get,expected_ids):
    schema=get('',{},'schema')
    fields={x['name']:x for x in schema['fields']}
    if (schema['name']!='p8-demo-prod'
        or set(fields)!={'chunk_id','artefact','content','vector'}
        or fields['vector']['retrievable'] is not False
        or not all(fields[k]['retrievable'] is True for k in ['chunk_id','artefact','content'])
        or fields['chunk_id']['key'] is not True or fields['chunk_id']['sortable'] is not True):
        raise ValueError('fingerprint schema mismatch')
    pages=[get('/docs',page_parameters(page),'documents-'+str(page).zfill(2)) for page in range(42)]
    return schema,verified_documents(pages,expected_ids)


def _completed_capture(directory,authority,receipt):
    """Re-read every original, not just two matching summary dictionaries."""
    from app.delivery_search.contracts import canonical,DeliveryRefused,sha256_file
    from .metadata import require_policy
    from .session import bound_file,instant
    policy=require_policy(authority)
    if (any(receipt.get(k)!=authority[k] for k in ('session','source_sha','release_digest'))
        or receipt.get('metadata_policy')!=authority['metadata_policy']): raise DeliveryRefused('authority')
    folder=bound_file(directory,receipt['folder'])
    captured=receipt['observations']
    labels=['schema']+['documents-'+str(i).zfill(2) for i in range(42)]
    if len(captured)!=43 or len({row['metadata_budget_claim'] for row in captured})!=43:
        raise DeliveryRefused('evidence')
    if set(receipt['reservation_originals'])!={row['metadata_budget_claim'] for row in captured}:
        raise DeliveryRefused('evidence')
    times=[]
    from urllib.parse import urlencode
    for i,(label,row) in enumerate(zip(labels,captured)):
        params={'api-version':'2024-07-01',**({} if i==0 else page_parameters(i-1))}
        expected='https://'+authority['search_host']+'/indexes/p8-demo-prod'+('' if i==0 else '/docs')
        claim=row['metadata_budget_claim']
        if (row['method']!='GET' or row['status']!=200 or row['file']!=label+'.json'
            or row['url']!=expected+'?'+urlencode(params)
            or not re.fullmatch(r'budgets/metadata/[0-9]{3}\.json',claim)
            or not 0<=int(claim.rsplit('/',1)[1][:3])<policy.workload
            or receipt['reservation_originals'][claim]!={'slot':int(claim.rsplit('/',1)[1][:3]),'budget':'metadata'}
            or sha256_file(folder/row['file'])!=row['sha256']): raise DeliveryRefused('evidence')
        times.append(instant(row['at']))
    if times!=sorted(times) or times[-1]>datetime.now(timezone.utc): raise DeliveryRefused('evidence')
    schema,docs=capture_search_originals(lambda suffix,params,label:json.loads((folder/(label+'.json')).read_bytes()),
        set(receipt['expected_ids']))
    state=json.loads((folder/'state.json').read_bytes())
    if not isinstance(state,dict): raise DeliveryRefused('evidence')
    hashes={'schema_sha256':sha(canonical(schema)), 'documents_sha256':sha(canonical(docs)),
        'state_sha256':sha(canonical(state))}
    if hashes!=receipt['fingerprints']: raise DeliveryRefused('evidence')
    return hashes,times[0],times[-1]


def complete_fingerprint(directory,authority,phase,folder,captured,expected_ids,state,*,store):
    """Seal only a completed43-GET capture. Call after state capture, before registry publication."""
    from app.delivery_search.contracts import canonical,exclusive_json,DeliveryRefused
    from .metadata import require_policy,require_no_local_metadata
    policy=require_policy(authority);require_no_local_metadata(directory)
    if store.metadata_policy!=policy or store.host!=authority['storage_host']: raise DeliveryRefused('authority')
    if phase not in ('before','after'): raise DeliveryRefused('authority')
    # This file must have been written from the actual state response by the caller.
    if json.loads((folder/'state.json').read_bytes())!=state: raise DeliveryRefused('evidence')
    schema,docs=capture_search_originals(lambda suffix,params,label:json.loads((folder/(label+'.json')).read_bytes()),expected_ids)
    receipt={k:authority[k] for k in ('session','source_sha','release_digest','metadata_policy')}
    receipt.update({'folder':str(folder.resolve().relative_to(directory.resolve())).replace('\\','/'),
        'observations':captured,'expected_ids':sorted(expected_ids),
        'reservation_originals':{row['metadata_budget_claim']:store.json('evidence',row['metadata_budget_claim'])
            for row in captured},
        'fingerprints':{'schema_sha256':sha(canonical(schema)), 'documents_sha256':sha(canonical(docs)),
            'state_sha256':sha(canonical(state))}})
    _completed_capture(directory,authority,receipt)
    if phase=='before':
        if (directory/'intents/dispatch-negative.json').exists(): raise DeliveryRefused('authority')
    else:
        _require_after(directory,authority,receipt)
    exclusive_json(directory/('fingerprint-'+phase+'-complete.json'),receipt)
    return receipt


def _require_after(directory,authority,after):
    from app.delivery_search.contracts import DeliveryRefused
    from .session import read,instant
    from .evidence import Originals
    before=read(directory/'fingerprint-before-complete.json')
    before_hashes,_,before_end=_completed_capture(directory,authority,before)
    after_hashes,after_start,_=_completed_capture(directory,authority,after)
    originals=Originals(directory,SimpleNamespace(**authority),authority)
    build=originals.build('negative','failed')
    gate=originals.json('gate-negative-receipt')
    if (before_hashes!=after_hashes or set(before['expected_ids'])!=set(after['expected_ids'])
        or {row['metadata_budget_claim'] for row in before['observations']}
            & {row['metadata_budget_claim'] for row in after['observations']}
        or not before_end<instant(build['queueTime'])<=instant(build['finishTime'])<after_start
        or any(gate.get(k)!=authority[k] for k in ('source_sha','release_digest'))
        or gate.get('run_id')!=build['id'] or gate.get('stage')!='negative'
        or gate.get('expected_negative') is not True or gate.get('acceptance_pass') is not True):
        raise DeliveryRefused('evidence')


def verify_completed_fingerprints(directory,authority):
    from .session import read
    from app.delivery_search.contracts import DeliveryRefused
    try:
        _require_after(directory,authority,read(directory/'fingerprint-after-complete.json'))
    except (KeyError,TypeError,ValueError,OSError):
        raise DeliveryRefused('evidence') from None
