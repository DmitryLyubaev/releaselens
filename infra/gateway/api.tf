# One API, "Azure OpenAI (v1)", in a version set with the segment scheme, so a later breaking
# change has a home as v2 (spec §3.2). Clients call /openai/v1/chat/completions. API Management
# forwards only the operation's path after the backend URL, so the backends' URLs end in
# /openai/v1 (plan ruling).
resource "azurerm_api_management_api_version_set" "openai" {
  name                = "azure-openai"
  display_name        = "Azure OpenAI"
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name
  versioning_scheme   = "Segment"
}

resource "azurerm_api_management_api" "v1" {
  name                = "azure-openai-v1"
  display_name        = "Azure OpenAI (v1)"
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name
  revision            = "1"
  path                = "openai"
  protocols           = ["https"]
  version             = "v1"
  version_set_id      = azurerm_api_management_api_version_set.openai.id

  # No subscription key: the Entra token the policy validates is the only credential, and no
  # product or subscription exists.
  subscription_required = false

  # No service_url: the policy routes to the backend pool. Without the policy, no request can
  # reach a model.
}

resource "azurerm_api_management_api_operation" "chat_completions" {
  operation_id        = "chat-completions"
  api_name            = azurerm_api_management_api.v1.name
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name
  display_name        = "Chat completions"
  method              = "POST"
  url_template        = "/chat/completions"
}

# Revision 2 (spec §4.3): non-current, called at /openai/v1;rev=2/chat/completions in the smoke
# test, then made current by the release below. Everything but the policy is revision 1's,
# written out so that nothing is left for the service to choose.
#
# The creation order matters, because a revision is a copy of its source as it is at that moment:
#   1. revision 1, its operation, its diagnostic and the diagnostic's metrics switch
#   2. revision 2, copied from that, so it has the operation and, if the service copies it, the
#      diagnostic with metrics on (a live check, in the README)
#   3. only then the two policies, each written to its own revision, so neither is a copy of the
#      other
resource "azurerm_api_management_api" "v1_rev2" {
  name                  = azurerm_api_management_api.v1.name
  display_name          = azurerm_api_management_api.v1.display_name
  resource_group_name   = azurerm_resource_group.gateway.name
  api_management_name   = azurerm_api_management.gateway.name
  revision              = "2"
  revision_description  = "Removes backend response headers a caller does not need."
  source_api_id         = azurerm_api_management_api.v1.id
  path                  = azurerm_api_management_api.v1.path
  protocols             = ["https"]
  version               = azurerm_api_management_api.v1.version
  version_set_id        = azurerm_api_management_api.v1.version_set_id
  subscription_required = false

  depends_on = [
    azurerm_api_management_api_operation.chat_completions,
    azurerm_api_management_api_diagnostic.appi,
    azapi_update_resource.diagnostic_metrics,
  ]
}

# The policies are files, so the static checks in tests/infra parse what is deployed. They are
# azapi, not azurerm_api_management_api_policy: in azurerm 5.7 that resource reads and deletes by
# the API's name with the ";rev=n" stripped, so on a revision it is replaced on every plan, and
# the replacement deletes the current revision's policy instead of its own. Here each policy's
# parent is its own revision's id, which ends in ";rev=1" or ";rev=2", so it always addresses
# that revision, current or not.
#
# format = "xml": the files are well-formed XML, with the generics in expressions escaped
# (&lt;JObject&gt;), which is what "xml" means. "rawxml" is for unescaped expressions.
resource "azapi_resource" "policy_v1" {
  type      = "Microsoft.ApiManagement/service/apis/policies@2024-05-01"
  name      = "policy"
  parent_id = azurerm_api_management_api.v1.id

  body = {
    properties = {
      format = "xml"
      value  = file("${path.module}/policies/api-v1.xml")
    }
  }

  # After revision 2 is copied (see above), so revision 2 starts with no policy of revision 1's.
  depends_on = [azurerm_api_management_api.v1_rev2]
}

resource "azapi_resource" "policy_v1_rev2" {
  type      = "Microsoft.ApiManagement/service/apis/policies@2024-05-01"
  name      = "policy"
  parent_id = azurerm_api_management_api.v1_rev2.id

  body = {
    properties = {
      format = "xml"
      value  = file("${path.module}/policies/api-v1-rev2.xml")
    }
  }
}

# Made only once revision 2 has passed the smoke test, with release_revision_2 = true. The
# release changes which revision is current, not what either holds: each policy stays on its own
# revision, so a later apply rewrites neither onto the other. The operation and the diagnostic
# address the current revision by name, so after the release they refer to revision 2's copies;
# read any later plan for them. The session still ends with the destroy.
resource "azurerm_api_management_api_release" "revision_2" {
  count = var.release_revision_2 ? 1 : 0

  name   = "revision-2"
  api_id = azurerm_api_management_api.v1_rev2.id
  notes  = "Revision 2 passed the smoke test."

  # Never make a revision current before its policy is in place: without it, the revision has no
  # token check.
  depends_on = [azapi_resource.policy_v1_rev2]
}
