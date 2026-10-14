#!/usr/bin/env bash
# GitHub-hosted Docker only. No Azure, registration or account credentials.
set -euo pipefail
docker run --rm --entrypoint bash -v "$PWD:/source:ro" "${1:?image required}" -c '
  set -euo pipefail
  test "$(id -u)" = 10001
  test -x /opt/azp/bin/Agent.Listener
  /opt/azp/bin/Agent.Listener --version
  git --version
  cp -a /source /tmp/work
  cd /tmp/work
  python3 -m venv /tmp/checks
  /tmp/checks/bin/pip install -r tests/infra/requirements.txt
  /tmp/checks/bin/python -m pytest tests/infra/test_agents_runtime.py -q
  for name in ReleaseLens.Core.Tests ReleaseLens.Web.Tests; do
    dotnet test "tests/$name/$name.csproj" --configuration Release
  done
'
