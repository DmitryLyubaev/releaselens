"""Workflow evidence bound to the exact GitHub artifact digest and run attempt."""
from datetime import datetime
import hashlib,io,json,re,stat,zipfile
from .contracts import utc_time
from .checks import observation_from_dict
from .http_client import _unique
from .private_az import TransportRefused

FIELDS={'schema','repository','source_sha','run_id','run_attempt','mode','fixture','measured_at','source_run_id','source_run_attempt','observation'}
REJECTION_FIELDS={'schema','repository','source_sha','run_id','run_attempt','mode','measured_at','source_run_id','source_run_attempt','source_sha_selected','reason'}

def _time(value):
    if not utc_time(value): raise ValueError()
    return datetime.fromisoformat(value.replace('Z','+00:00'))

def _binding(value,run,repository):
    if value['schema']!=1 or type(value['schema']) is not int or value['repository']!=repository or value['source_sha']!=run['head_sha'] or not re.fullmatch(r'[a-f0-9]{40}',value['source_sha']): raise ValueError()
    if any(type(value[key]) is not int or value[key]<=0 or value[key]!=run[other] for key,other in [('run_id','id'),('run_attempt','run_attempt')]): raise ValueError()
    if run['repository']['full_name']!=repository or run['head_repository']['full_name']!=repository or run['head_branch']!='main' or run['event']!='workflow_dispatch' or run['path']!='.github/workflows/webapp-release.yml' or run['status']!='completed': raise ValueError()
    if not _time(run['created_at'])<=_time(value['measured_at'])<=_time(run['updated_at']): raise ValueError()

def emit(observation,*,repository,sha,run_id,attempt,mode,fixture,measured_at,source_run_id=None,source_run_attempt=None):
    return dict(schema=1,repository=repository,source_sha=sha,run_id=run_id,run_attempt=attempt,mode=mode,fixture=fixture,measured_at=measured_at,source_run_id=source_run_id,source_run_attempt=source_run_attempt,observation=observation.to_dict())

def observation(value,run,repository,*,mode,fixture,source_run=None):
    try:
        if not isinstance(value,dict) or set(value)!=FIELDS: raise ValueError()
        _binding(value,run,repository)
        if value['mode']!=mode or type(value['fixture']) is not bool or value['fixture'] is not fixture: raise ValueError()
        if run['display_title']!=f"webapp-{mode}-{'fixture' if fixture else 'candidate'}-{run['head_sha']}-{run['run_attempt']}": raise ValueError()
        if mode=='promote':
            if source_run is None or type(value['source_run_id']) is not int or type(value['source_run_attempt']) is not int or value['source_run_id']!=source_run['id'] or value['source_run_attempt']!=source_run['run_attempt']: raise ValueError()
        elif value['source_run_id'] is not None or value['source_run_attempt'] is not None: raise ValueError()
        return observation_from_dict(value['observation'])
    except Exception: raise TransportRefused('invalid_response') from None

def rejection(value,run,selected,repository):
    try:
        if not isinstance(value,dict) or set(value)!=REJECTION_FIELDS: raise ValueError()
        _binding(value,run,repository)
        if value['mode']!='promote' or value['reason']!='fixture_ineligible' or value['source_run_id']!=selected['id'] or type(value['source_run_id']) is not int or value['source_run_attempt']!=selected['run_attempt'] or type(value['source_run_attempt']) is not int or value['source_sha_selected']!=selected['head_sha']: raise ValueError()
        if selected['conclusion']!='failure' or selected['status']!='completed' or selected['display_title']!=f"webapp-stage-fixture-{selected['head_sha']}-{selected['run_attempt']}": raise ValueError()
        if selected['repository']['full_name']!=repository or selected['head_repository']['full_name']!=repository or selected['head_branch']!='main' or selected['path']!='.github/workflows/webapp-release.yml' or selected['event']!='workflow_dispatch': raise ValueError()
        return True
    except Exception: raise TransportRefused('invalid_response') from None

def artifact(api,run,name,path):
    """Read the owner's original downloaded ZIP only after checking GitHub's digest.

    Never extract it or execute artifact code. A missing digest refuses evidence.
    """
    try:
        if not re.fullmatch(r'webapp-(?:observations|stage|rejection)-'+str(run['id'])+'-'+str(run['run_attempt']),name): raise ValueError()
        metadata=api._get(f"actions/runs/{run['id']}/artifacts?per_page=100")
        if type(metadata.get('total_count')) is not int or metadata['total_count']>100: raise ValueError()
        candidates=[item for item in metadata['artifacts'] if item.get('name')==name]
        if len(candidates)!=1: raise ValueError()
        item=candidates[0]; binding=item['workflow_run']
        if item['expired'] is not False or binding['id']!=run['id'] or binding['head_sha']!=run['head_sha'] or binding['head_branch']!='main' or not _time(run['created_at'])<=_time(item['created_at'])<=_time(run['updated_at']): raise ValueError()
        digest=item['digest']
        if not isinstance(digest,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',digest) or path.is_symlink() or path.stat().st_size>220*1024*1024: raise ValueError()
        data=path.read_bytes()
        if 'sha256:'+hashlib.sha256(data).hexdigest()!=digest: raise ValueError()
        result={}
        with zipfile.ZipFile(io.BytesIO(data)) as zipped:
            entries=zipped.infolist(); names=[entry.filename for entry in entries]
            allowed={'receipt.json','package.zip','rejection.json','observation-W2.json','observation-W4.json','observation-W5.json','observation-W6a.json'}
            if not entries or len(names)!=len(set(names)) or not set(names)<=allowed or sum(entry.file_size for entry in entries)>220*1024*1024 or any(stat.S_ISLNK(entry.external_attr>>16) for entry in entries): raise ValueError()
            for entry in entries:
                if entry.filename=='package.zip': continue
                if entry.file_size>1024*1024: raise ValueError()
                result[entry.filename]=json.loads(zipped.read(entry),object_pairs_hook=_unique)
        return result
    except Exception: raise TransportRefused('invalid_response') from None
