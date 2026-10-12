"""Bounded owner attribution. Private query rows are never report fields."""
from datetime import datetime,timezone
import math
import re
import httpx
from app.gateway.metric import QUERY_URL, METRIC_NAME, API_ID
from .http_client import _body, _unique
from .private_az import TransportRefused
import json

def _bounds(start,end):
    if not isinstance(start,datetime) or not isinstance(end,datetime) or start.utcoffset() is None or end.utcoffset() is None or not 0<(end-start).total_seconds()<=900:
        raise TransportRefused('invalid_response')
    return tuple(item.astimezone(timezone.utc).isoformat().replace('+00:00','Z') for item in (start,end))

def owner_matches(rows: list[dict],owner_oid: str,start: datetime,end: datetime) -> tuple[bool,int]:
    _bounds(start,end); count=0
    for row in rows:
        try:
            stamp=datetime.fromisoformat(row['timestamp'].replace('Z','+00:00'))
            total=row['total']
            if row['Caller']==owner_oid and stamp.utcoffset() is not None and start<=stamp<=end and type(total) in (float,int) and math.isfinite(total) and total>0: count+=1
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError): continue
    return count>0,count

def query_window(http:httpx.Client,app_id:str,bearer:str,start:datetime,end:datetime)->list[dict]:
    since,until=_bounds(start,end)
    if not isinstance(app_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{1,128}',app_id): raise TransportRefused('invalid_response')
    query=('customMetrics\n'+f'| where timestamp >= datetime({since})\n| where timestamp <= datetime({until})\n'
           +f'| where name == "{METRIC_NAME}"\n| where tostring(customDimensions["API ID"]) == "{API_ID}"\n'
           +'| extend Caller = tostring(customDimensions["Caller"])\n| project timestamp, Caller, total=valueSum')
    try:
        request=http.build_request('POST',QUERY_URL.format(app_id=app_id),json={'query':query,'timespan':f'{since}/{until}'},headers={'Authorization':'Bearer '+bearer},timeout=15)
        request.headers.pop('Cookie',None)
        response=http.send(request,stream=True,follow_redirects=False,auth=None)
        try:
            if response.status_code!=200: raise ValueError()
            data=json.loads(_body(response),object_pairs_hook=_unique)
            if 'error' in data or len(data['tables'])!=1: raise ValueError()
            table=data['tables'][0]; names=[column['name'] for column in table['columns']]
            if set(names)!={'timestamp','Caller','total'} or len(names)!=3: raise ValueError()
            return [dict(zip(names,row,strict=True)) for row in table['rows']]
        finally: response.close()
    except (httpx.HTTPError,OSError,ValueError,TypeError,KeyError,IndexError): raise TransportRefused('invalid_response') from None
