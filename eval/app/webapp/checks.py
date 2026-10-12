"""The fixed conjunctions. Missing facts and wrong provenance never pass."""
from .contracts import CheckResult, Observation, ContractRefused, REQUIRED_MATCHES
from .release import ci_passed

KINDS = {'W1':{'live'},'W2':{'mixed'},'W3':{'live'},'W4':{'workflow'},'W5':{'workflow'},'W6a':{'live','mixed'},'W6b':{'live'}}

def evaluate(check: str, observation: Observation, *, commit: str, evidence: str, measured_at: str) -> CheckResult:
    if check not in REQUIRED_MATCHES or not isinstance(observation,Observation) or observation.check != check: raise ContractRefused()
    passed = evidence in KINDS[check] and all(observation.matches.get(key) is True for key in REQUIRED_MATCHES[check])
    return CheckResult(check,'pass' if passed else 'fail',evidence,(observation,),commit,measured_at)

def offline_edges(metadata: dict, commit: str, repository: str) -> bool:
    if not ci_passed(metadata,repository,commit): return False
    try:
        jobs=[job for job in metadata['jobs'] if job.get('name')=='build-and-test']
        steps=[step for step in jobs[0].get('steps',[]) if step.get('name')=='Webapp offline token and route edges']
        return len(steps)==1 and steps[0].get('status')=='completed' and steps[0].get('conclusion')=='success'
    except (KeyError,TypeError,IndexError,AttributeError): return False

def observation_from_dict(data: dict) -> Observation:
    if not isinstance(data,dict) or set(data)!={'check','statuses','matches','counts','times'}: raise ContractRefused()
    return Observation(**data)

def result_from_dict(data: dict) -> CheckResult:
    if not isinstance(data,dict) or set(data)!={'check','verdict','evidence','observations','commit','measured_at'} or not isinstance(data['observations'],list): raise ContractRefused()
    return CheckResult(**(data|{'observations':tuple(observation_from_dict(item) for item in data['observations'])}))
