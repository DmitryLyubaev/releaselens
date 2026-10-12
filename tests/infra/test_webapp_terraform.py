"""Keep the web session stack small, keyless and unable to widen its trust."""
import re
from pathlib import Path
import pytest


def code(repo_root: Path) -> str:
    paths = list((repo_root / "infra/webapp").glob("*.tf"))
    assert paths, "Session stack must exist"
    return "\n".join(path.read_text() for path in paths)


def test_no_session_identity_secret_or_retrieval_stack(repo_root: Path):
    text = code(repo_root)
    resources = re.findall(r'resource\s+"([^"]+)"', text)
    assert set(resources) == {"azurerm_resource_group", "azurerm_service_plan", "azurerm_role_assignment", "azapi_resource", "azapi_update_resource"}
    for forbidden in ("client_secret", "password", "certificate", "WEBSITE_OVERRIDE_STICKY", "autoSwapSlotName", "SearchService", "functionapp", "WEBSITE_RUN_FROM_PACKAGE_BLOB"):
        assert forbidden not in text
    assert not re.search(r'\bclientSecret\s*=', text)


@pytest.mark.parametrize("name", ["webapp_resource_id", "webapp_main_url", "webapp_staging_url", "webapp_frontend_audience"])
def test_session_identifier_outputs_are_sensitive(repo_root: Path, name: str):
    text = (repo_root / "infra/webapp/outputs.tf").read_text()
    output = re.search(rf'output\s+"{name}"\s*\{{(.*?)\n\}}', text, re.S)
    assert output is not None
    assert re.search(r'\bsensitive\s*=\s*true', output.group(1))


def test_terraform_does_not_own_selected_package_or_build_version(repo_root: Path):
    text = code(repo_root)
    # Selection lives in SCM's SitePackages manifest, outside ARM properties.
    assert not re.search(r'(?i)(packageUri|packageUrl|zipDeploy|packagename\.txt|APP_VERSION|BUILD_VERSION)\s*=', text)
    assert 'WEBSITE_RUN_FROM_PACKAGE' in text
    assert not re.search(r'WEBSITE_RUN_FROM_PACKAGE\s*=\s*"https?:', text)
