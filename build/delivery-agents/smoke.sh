#!/usr/bin/env bash
# Actual GitHub-hosted container execution. No registration, Azure login or model call.
set -euo pipefail
docker run --rm --entrypoint bash -v "$PWD:/source:ro" "${1:?image required}" -c '
  set -euo pipefail
  test "$(id -u)" = 10001
  test "$P8Agent" = ReleaseLens
  export AZURE_CONFIG_DIR=/tmp/p8-empty-azure
  az version --output json > /tmp/cli-version.json
  terraform version -json > /tmp/tf-version.json
  python -c "import json; assert json.load(open(\"/tmp/cli-version.json\"))[\"azure-cli\"] == \"2.81.0\"; assert json.load(open(\"/tmp/tf-version.json\"))[\"terraform_version\"] == \"1.15.8\""
  python -c "import azure.identity, httpx, numpy, pydantic, yaml, pytest, anthropic, truststore"
  /opt/azp/bin/Agent.Listener --version
  cp -a /source /tmp/work
  cd /tmp/work
  python -m pytest tests/infra/test_agents_runtime.py tests/infra/test_pipelines_image.py -q
  cd eval
  python -m pytest tests/delivery_search tests/retrieval -q
  cd ..
  for root in identity foundation index; do
    terraform -chdir="infra/pipelines/$root" init -backend=false -input=false -lockfile=readonly
    terraform -chdir="infra/pipelines/$root" validate
    terraform -chdir="infra/pipelines/$root" test
  done
'
