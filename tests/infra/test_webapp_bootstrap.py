"""Prevent extra credentials, gateway privileges or public identity outputs."""
import re
from pathlib import Path

OUTPUTS = (
    "webapp_name", "webapp_identity_id", "webapp_identity_client_id",
    "webapp_identity_principal_id", "webapp_app_client_id",
    "webapp_app_identifier_uri", "webapp_owner_object_id", "deploy_identity_principal_id",
)


def test_frontend_enables_id_token_for_easy_auth_hybrid_sign_in(repo_root: Path):
    text = (repo_root / "infra/bootstrap/webapp.tf").read_text()
    # Easy Auth requests code+id_token. Both callback slots share this web client.
    grant = re.search(r'implicit_grant\s*\{([^}]+)\}', text)
    assert grant is not None, "Easy Auth hybrid sign-in requires ID-token issuance"
    assert re.search(r'\bid_token_issuance_enabled\s*=\s*true\b', grant.group(1))
    assert re.search(r'\baccess_token_issuance_enabled\s*=\s*false\b', grant.group(1))


def test_frontend_has_no_password_certificate_or_github_trust(repo_root: Path):
    text = (repo_root / "infra/bootstrap/webapp.tf").read_text()
    resources = re.findall(r'resource\s+"([^"]+)"', text)
    assert "azuread_application" in resources
    assert "azuread_application_federated_identity_credential" in resources
    assert not any("password" in item or "certificate" in item for item in resources)
    assert "azurerm_federated_identity_credential" not in resources
    assert "token.actions.githubusercontent.com" not in text


def test_frontend_identity_has_no_gateway_or_model_role(repo_root: Path):
    text = "\n".join(path.read_text() for path in (repo_root / "infra/bootstrap").glob("*.tf"))
    # Assignment resources have flat bodies. Comments cannot invent an assignment.
    for kind, body in re.findall(r'resource\s+"(azuread_app_role_assignment|azurerm_role_assignment)"\s+"[^"]+"\s*\{([^}]+)\}', text):
        if re.search(r'principal(?:_object)?_id\s*=\s*azurerm_user_assigned_identity\.webapp\.principal_id', body):
            assert "gateway_invoke_role" not in body
            assert "Cognitive Services" not in body
    assert (repo_root / "infra/bootstrap/webapp.tf").exists()


def test_frontend_outputs_are_sensitive(repo_root: Path):
    text = (repo_root / "infra/bootstrap/outputs.tf").read_text()
    for name in OUTPUTS:
        match = re.search(rf'output\s+"{name}"\s*\{{(.*?)\n\}}', text, re.S)
        assert match is not None, f"Missing protected output: {name}"
        assert re.search(r'\bsensitive\s*=\s*true\b', match.group(1)), f"Unprotected output: {name}"
