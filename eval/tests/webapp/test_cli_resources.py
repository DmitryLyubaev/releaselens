import pytest
from app.webapp.__main__ import validate_refs
from app.webapp.private_az import TransportRefused

SUB='00000000-0000-0000-0000-000000000001'
PREFIX='/subscriptions/'+SUB
def refs():
    group=PREFIX+'/resourceGroups/session-test'
    site=group+'/providers/Microsoft.Web/sites/web-test'
    return dict(webapp_group=group,gateway_group=PREFIX+'/resourceGroups/gateway-test',plan=group+'/providers/Microsoft.Web/serverfarms/plan-test',site=site,slot=site+'/slots/staging',deleted_apim=PREFIX+'/providers/Microsoft.ApiManagement/locations/australiaeast/deletedservices/gateway-test',bootstrap=[PREFIX+'/resourceGroups/bootstrap-test'])

def test_only_canonical_expected_resource_shapes_and_subscription_can_prove_absence():
    assert validate_refs(refs(),SUB)==refs()
    for key in ('site','plan','slot','deleted_apim','webapp_group','gateway_group'):
        value=refs(); value[key]=PREFIX+'/made-up/'+key
        with pytest.raises(TransportRefused): validate_refs(value,SUB)
    with pytest.raises(TransportRefused): validate_refs(refs(),'different-sub')
