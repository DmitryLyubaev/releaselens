"""deploy.yml's smoke step masks the connection string and the issued key before anything else
can use them. GitHub masks a value only from the moment `::add-mask::` registers it, and this
repository's run logs are public."""

import re

import pytest
import yaml


def _statements(script):
    """The script's logical lines: comments and blank lines dropped, `\\` continuations joined."""
    statements, pending = [], ""
    for raw in script.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        statements.append(pending + line)
        pending = ""
    return statements


def _uses(statement, name):
    return re.search(rf"\$\{{?{name}\b", statement) is not None


def _index(statements, pattern):
    matches = [i for i, statement in enumerate(statements) if re.fullmatch(pattern, statement)]
    assert len(matches) == 1, f"expected one statement matching {pattern!r}, found {len(matches)}"
    return matches[0]


@pytest.fixture
def smoke(repo_root):
    workflow = yaml.safe_load((repo_root / ".github" / "workflows" / "deploy.yml").read_bytes())
    steps = [step for step in workflow["jobs"]["deploy"]["steps"] if step.get("name") == "Smoke test"]
    assert len(steps) == 1
    return _statements(steps[0]["run"])


def test_the_connection_string_is_masked_as_soon_as_it_is_read(smoke):
    read = _index(smoke, r'db=\$\(terraform .*output -raw database_connection_string\)')
    assert smoke[read + 1] == 'mask "$db"'
    assert not any(_uses(statement, "db") for statement in smoke[:read])


def test_the_issued_key_is_masked_as_soon_as_it_is_captured(smoke):
    issued = _index(smoke, r'issued=\$\(.*issue-key .*\)')
    key = _index(smoke, r'key=\$\(.*"\$issued".*\)')
    # issue-key's output holds the key, so it may feed only the line that extracts the key.
    assert key == issued + 1
    assert smoke[key + 1] == 'mask "$key"'
    assert not any(_uses(statement, "issued") for statement in smoke[:key])
    assert not any(_uses(statement, "key") for statement in smoke[:key + 1])
