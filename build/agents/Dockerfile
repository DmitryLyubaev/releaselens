FROM mcr.microsoft.com/dotnet/sdk:10.0.201-noble@sha256:c804b88aaca8d6b76f65998427adec3101dcbbf2e52e705e2509eb81263aab0c
RUN test "$(uname -m)" = x86_64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv git curl ca-certificates \
       libicu74 libssl3t64 libkrb5-3 zlib1g liblttng-ust1t64 libunwind8 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 agent \
    && useradd --uid 10001 --gid 10001 --create-home agent
COPY versions.json /tmp/versions.json
RUN mkdir -p /opt/azp \
    && python3 -c 'import json,subprocess; v=json.load(open("/tmp/versions.json")); subprocess.run(["curl","--fail","--location","--silent","--show-error",v["agent_url"],"-o","/tmp/agent.tgz"],check=True); subprocess.run(["sha256sum","-c"],input=v["agent_sha256"]+"  /tmp/agent.tgz\n",text=True,check=True)' \
    && tar xzf /tmp/agent.tgz -C /opt/azp \
    && rm /tmp/agent.tgz /tmp/versions.json \
    && chown -R agent:agent /opt/azp
COPY --chown=agent:agent start.py /opt/azp/start.py
USER 10001:10001
WORKDIR /opt/azp
ENV HOME=/home/agent DOTNET_NOLOGO=true DOTNET_SKIP_FIRST_TIME_EXPERIENCE=true P7Agent=ReleaseLens
ENTRYPOINT ["python3", "/opt/azp/start.py"]
