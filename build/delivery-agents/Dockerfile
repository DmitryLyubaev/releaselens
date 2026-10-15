FROM ghcr.io/dmitrylyubaev/releaselens-agent@sha256:3167931608ce2390fdc565d737074c9aaa57098f2099aabffe9f2fc431d94c82
USER root
COPY build/delivery-agents/versions.json build/delivery-agents/requirements.lock build/delivery-agents/cli-requirements.lock build/delivery-agents/install.py /tmp/p8/
RUN python3 /tmp/p8/install.py /tmp/p8 --terraform-out /usr/local/bin/terraform \
    && python3 -m venv /opt/p8-cli \
    && /opt/p8-cli/bin/pip install --disable-pip-version-check --require-hashes --only-binary=:all: --no-deps -r /tmp/p8/cli-requirements.lock \
    && python3 -m venv /opt/p8-workload \
    && /opt/p8-workload/bin/pip install --disable-pip-version-check --require-hashes --only-binary=:all: --no-deps -r /tmp/p8/requirements.lock \
    && rm -rf /tmp/p8 /root/.cache/pip
USER 10001:10001
ENV PATH=/opt/p8-workload/bin:/opt/p8-cli/bin:$PATH P8Agent=ReleaseLens \
    AZURE_CORE_COLLECT_TELEMETRY=false AZURE_CORE_CHECK_VERSION=false AZURE_EXTENSION_USE_DYNAMIC_INSTALL=no
# The pinned parent supplies the one-shot ENTRYPOINT; do not replace its lifecycle.
