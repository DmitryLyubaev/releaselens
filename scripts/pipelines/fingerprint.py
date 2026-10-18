"""Read complete ordered index fingerprints within the shared metadata budget.

Callers supply reviewed authority, transport, deadlines and create-only evidence paths.
"""
from datetime import datetime, timezone
import hashlib


def sha(data):
    return hashlib.sha256(data).hexdigest()

def fingerprint_capacity(ordinary_used,phase):
    # Keep the physical tail and the remaining negative/Verify count reads.
    if (phase not in ('before','after') or type(ordinary_used) is not int or ordinary_used<0
            or ordinary_used+(86 if phase=='before' else 43)+(2 if phase=='before' else 1)>108):
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
    from scripts.pipelines.storage import METADATA_OPERATIONS
    claim=store.reserve_budget('metadata',METADATA_OPERATIONS)
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
