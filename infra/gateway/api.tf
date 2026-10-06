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

# The policy is a file, so the static checks in tests/infra parse what is deployed. api_name
# without a revision addresses the current revision, which is revision 1 until revision 2 is
# released.
resource "azurerm_api_management_api_policy" "v1" {
  api_name            = azurerm_api_management_api.v1.name
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name
  xml_content         = file("${path.module}/policies/api-v1.xml")
}

# Revision 2 (spec §4.3): non-current, called at /openai/v1;rev=2/chat/completions in the smoke
# test, then made current by the release below. Everything but the policy is revision 1's,
# written out so that nothing is left for the service to choose.
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

  # A revision copies its source's operations when it is created, so the operation must exist
  # first.
  depends_on = [azurerm_api_management_api_operation.chat_completions]
}

# The provider reads api_name back without the ";rev=2" (azurerm 5.7, getApiName in the
# policy resource's read), and api_name forces replacement, so every plan after the first shows
# this policy replaced. That is noise, not drift: the replacement writes the same file to the
# same revision. Ignoring the change instead would make a later update address the current
# revision, which is the wrong one.
resource "azurerm_api_management_api_policy" "v1_rev2" {
  api_name            = "${azurerm_api_management_api.v1_rev2.name};rev=${azurerm_api_management_api.v1_rev2.revision}"
  resource_group_name = azurerm_resource_group.gateway.name
  api_management_name = azurerm_api_management.gateway.name
  xml_content         = file("${path.module}/policies/api-v1-rev2.xml")
}

# Made only once revision 2 has passed the smoke test, with release_revision_2 = true. After it,
# the next step is the destroy: revision 1's policy resource addresses the current revision, so
# another apply would write revision 1's policy over revision 2's.
resource "azurerm_api_management_api_release" "revision_2" {
  count = var.release_revision_2 ? 1 : 0

  name   = "revision-2"
  api_id = azurerm_api_management_api.v1_rev2.id
  notes  = "Revision 2 passed the smoke test."

  # Never make a revision current while its policy is being replaced (see above): without it,
  # the revision has no token check.
  depends_on = [azurerm_api_management_api_policy.v1_rev2]
}
