"""No tenant, client or object ID, and no Azure hostname of ours, in a public document.

This repository is public, and its runbooks say again and again that no identifier goes into a
file. That rule is kept by hand, so this scan holds it: it reads `README.md`, every Markdown file
under `docs/` and every `README.md` under `infra/`, and fails on

- a GUID that is not a placeholder, and
- a hostname under `azure.com`, `azure-api.net`, `windows.net` or `azure.net` that is neither a
  public endpoint named below, nor a template (`<account>.openai.azure.com`), nor an example name.

The allowed ones are listed here, on purpose. A new public endpoint or example name is added to
the list with a reason, in a commit a reviewer sees; nothing else gets through.
"""

import re
from pathlib import Path

import pytest

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# A name ending in one of the four domains, with the characters a template or a shell variable can
# add in front (`<account>`, `${...}`), and a bare leading dot for a suffix written on its own.
HOSTNAME = re.compile(r"[A-Za-z0-9<>{}$._-]*\.(?:azure\.com|azure-api\.net|windows\.net|azure\.net)(?![A-Za-z0-9-])",
                      re.IGNORECASE)

# Public endpoints that are the same for everyone: token audiences and the Resource Manager. They
# identify no tenant, subscription or account.
PUBLIC_ENDPOINTS = {
    "ai.azure.com",                 # the token audience for Azure OpenAI v1 through the Foundry endpoint
    "cognitiveservices.azure.com",  # the token audience for the gateway's call to a model
    "management.azure.com",         # the Azure Resource Manager
    "search.azure.com",             # the token audience for Azure AI Search
}
# Names the repository's own unit tests use for an account that does not exist.
EXAMPLE_HOSTS = {
    "example-subdomain.openai.azure.com",
    "releaselens-aoai.openai.azure.com",
    "releaselens-test.openai.azure.com",
}
# The placeholder suffix the plans use where a real account's name has six random characters.
PLACEHOLDER_SUFFIX = "a1b2c3"


def _is_placeholder_guid(guid: str) -> bool:
    """All but the last two hex digits are one digit: 0000..., 1111..., 2222..., 0000...0001, ...00a1."""
    digits = guid.replace("-", "").lower()
    return len(set(digits[:-2])) == 1


def _is_allowed_host(host: str) -> bool:
    lowered = host.lower()
    return (
        lowered in PUBLIC_ENDPOINTS
        or lowered in EXAMPLE_HOSTS
        or PLACEHOLDER_SUFFIX in lowered
        or lowered.startswith(".")                 # a suffix on its own, such as `.openai.azure.com`
        or any(mark in lowered for mark in "<>{}$")  # a template: `<account>.openai.azure.com`
    )


def identifiers_in(text: str) -> list[str]:
    """The kinds of identifier in `text` that are not placeholders, each with the text found."""
    found = [f"GUID {guid}" for guid in GUID.findall(text) if not _is_placeholder_guid(guid)]
    found += [f"hostname {host}" for host in HOSTNAME.findall(text) if not _is_allowed_host(host)]
    return found


def _public_documents(repo_root: Path) -> list[Path]:
    paths = [repo_root / "README.md"]
    paths += sorted((repo_root / "docs").rglob("*.md"))
    paths += sorted(p for p in (repo_root / "infra").rglob("README.md") if ".terraform" not in p.parts)
    return paths


def test_the_scan_covers_the_public_documents(repo_root: Path) -> None:
    names = {path.relative_to(repo_root).as_posix() for path in _public_documents(repo_root)}
    assert {"README.md", "docs/runbook-gateway.md", "docs/architecture.md", "infra/gateway/README.md",
            "infra/bootstrap/README.md"} <= names
    assert any(name.startswith("docs/superpowers/specs/") for name in names)
    assert any(name.startswith("docs/superpowers/plans/") for name in names)


def test_no_public_document_holds_an_identifier(repo_root: Path) -> None:
    problems = []
    for path in _public_documents(repo_root):
        for item in identifiers_in(path.read_text(encoding="utf-8")):
            problems.append(f"{path.relative_to(repo_root).as_posix()}: {item}")
    assert not problems, "an identifier is in a public document:\n" + "\n".join(problems)


@pytest.mark.parametrize("text", [
    "tenant 3f2c9a1e-7b4d-4c58-9e21-0d6a5b8c4f17",
    "object id 9A1B2C3D-0000-4000-8000-ABCDEF012345",
    "account aoai-releaselens-sea-q7x9k2.openai.azure.com",
    "gateway https://apim-releaselens-x7k2p9.azure-api.net/openai/v1/",
    "storage strelaselens4f2a.blob.core.windows.net",
    "vault kv-releaselens.vault.azure.net",
    "search srch-releaselens-8k3d2f.search.windows.net",
    "the first account: aoai-releaselens-q7x9k2.openai.azure.com",
])
def test_the_scan_catches_a_real_looking_identifier(text: str) -> None:
    assert identifiers_in(text), text


@pytest.mark.parametrize("text", [
    "tenant 00000000-0000-0000-0000-000000000000, client 11111111-1111-1111-1111-111111111111",
    "object id 00000000-0000-0000-0000-0000000000a1",
    "account aoai-releaselens-sea-a1b2c3.openai.azure.com",
    "template https://<account>.openai.azure.com/openai/v1/ and ${var.name}.openai.azure.com",
    "suffix `.azure-api.net` and `.openai.azure.com`",
    "scope https://cognitiveservices.azure.com and https://ai.azure.com/.default",
    "the Resource Manager at https://management.azure.com/subscriptions",
    "a unit test's account releaselens-aoai.openai.azure.com",
    "plain prose with no identifier in it: azure, the cloud",
])
def test_the_scan_lets_a_placeholder_or_a_public_endpoint_through(text: str) -> None:
    assert identifiers_in(text) == []


def test_the_gateway_runbook_never_prints_the_signed_in_account(repo_root: Path) -> None:
    # `az account show` prints the account's email unless its output is captured: the runbook's own
    # rule forbids an email on a screen, so each use must be a capture, and `acctok` prints a Boolean.
    text = (repo_root / "docs" / "runbook-gateway.md").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "az account show" in line and "`az account show`" not in line]
    assert lines, "the account check is missing"
    for line in lines:
        assert "ConvertFrom-Json" in line or "$(az account show" in line or "=$(az account show" in line, line
        assert "-o table" not in line, line
    assert "acctok" in text
