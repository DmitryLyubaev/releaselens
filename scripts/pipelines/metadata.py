"""Explicit owner-approved Search controller policy, never a provider HTTP cap."""
from dataclasses import asdict, dataclass

from app.delivery_search.contracts import DeliveryRefused


@dataclass(frozen=True)
class MetadataPolicy:
    version: str = 'controller-search-get-v2'
    total: int = 256
    ordinary: int = 244
    physical: int = 12
    cleanup_contingency: int = 9
    readiness_reads_per_loop: int = 24
    counting_boundary: str = 'python-controller-and-owner-search-get'
    provider_boundary: str = 'manifest-bound-two-index-lifecycles-and-original-command-deadlines'

    @property
    def workload(self):
        return self.ordinary - self.cleanup_contingency


def validate_policy(value):
    expected = asdict(MetadataPolicy())
    if (not isinstance(value, dict) or set(value) != set(expected)
        or any(type(value[k]) is not type(v) or value[k] != v for k, v in expected.items())):
        raise DeliveryRefused('authority')
    return MetadataPolicy()  # An immutable copy, independent of caller mutation.


def require_policy(authority):
    return validate_policy(authority.get('metadata_policy'))


def require_no_local_metadata(directory):
    if any((directory / 'intents').glob('budget-metadata-*.json')):
        raise DeliveryRefused('authority')


def require_cleanup_contingency(directory, authority):
    """Only owner cleanup can open the nine slots; workers cannot call this path."""
    require_policy(authority)
    from .session import read
    from .cleanup import early_allowed
    stop_path = directory / 'owner-stop-authority.json'
    if stop_path.exists():
        stop = read(stop_path)
        request = read(directory / 'quiescence/request.json')
        if (early_allowed(stop, authority['source_sha'], authority['session'])
            and all(request.get(k) == authority[k] for k in ('session', 'source_sha', 'manifest_sha256'))
            and request.get('kind') == 'owner-browser-quiescence-request'):
            return
        raise DeliveryRefused('authority')
    from .fingerprint import verify_completed_fingerprints
    verify_completed_fingerprints(directory, authority)
