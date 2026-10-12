"""Label-only output. W6 requires both settings and confirmed teardown."""
import re
from app.gateway.report import write
from .contracts import CheckResult,ContractRefused,REQUIRED_MATCHES
from .checks import evaluate

ORDER=('W1','W2','W3','W4','W5','W6a','W6b')

def render(results,commit:str)->str:
    if not isinstance(commit,str) or not re.fullmatch(r'[a-f0-9]{40}',commit): raise ContractRefused()
    by_name={}
    for result in results:
        if not isinstance(result,CheckResult) or result.commit!=commit or result.check in by_name: raise ContractRefused()
        if result.verdict!='not run':
            combined={key:value for observation in result.observations for key,value in observation.matches.items()}
            # Conflicting observations cannot hide a failed fact behind a later true one.
            for key in combined:
                combined[key]=all(observation.matches[key] for observation in result.observations if key in observation.matches)
            from .contracts import Observation
            expected=evaluate(result.check,Observation(result.check,matches=combined),commit=commit,evidence=result.evidence,measured_at=result.measured_at)
            if expected.verdict!=result.verdict: raise ContractRefused()
        by_name[result.check]=result
    lines=['# Project 6: Webapp evidence','','| Check | Verdict | Evidence |','|---|---|---|']
    for name in ORDER:
        result=by_name.get(name)
        lines.append(f'| {name} | {result.verdict if result else "not run"} | {result.evidence if result else "none"} |')
    halves=[by_name.get(name) for name in ('W6a','W6b')]
    w6='fail' if any(item and item.verdict=='fail' for item in halves) else 'pass' if all(item and item.verdict=='pass' for item in halves) else 'not run'
    lines+=['',f'W6: {w6}', '', 'W2 combines separately recorded live access facts and offline token/route tests for the same source commit.',
            'W5 is the negative release-gate check; its pass requires a failed fixture stage and an actually rejected promotion.',
            '',f'Measured commit: `{commit}`. Missing facts remain not run. No token, answer, identifier or runtime configuration appears here.','']
    return '\n'.join(lines)
