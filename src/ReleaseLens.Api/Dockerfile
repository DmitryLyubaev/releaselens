# syntax=docker/dockerfile:1

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src

# Restore as its own layer so a source-only change does not re-restore.
COPY Directory.Build.props Directory.Packages.props global.json ./
COPY src/ReleaseLens.Core/*.csproj        src/ReleaseLens.Core/
COPY src/ReleaseLens.Storage/*.csproj     src/ReleaseLens.Storage/
COPY src/ReleaseLens.Embedding/*.csproj   src/ReleaseLens.Embedding/
COPY src/ReleaseLens.Ingestion/*.csproj   src/ReleaseLens.Ingestion/
COPY src/ReleaseLens.Llm/*.csproj         src/ReleaseLens.Llm/
COPY src/ReleaseLens.Api/*.csproj         src/ReleaseLens.Api/
RUN dotnet restore src/ReleaseLens.Api/ReleaseLens.Api.csproj

COPY src/ src/
RUN dotnet publish src/ReleaseLens.Api/ReleaseLens.Api.csproj -c Release -o /app/publish --no-restore

# The embedding weights are a separate layer: ~130 MB that changes far less often
# than the application code, so it stays cached across almost every rebuild.
FROM curlimages/curl:8.21.0 AS model
WORKDIR /model
RUN curl -sSL -o model.onnx     https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main/onnx/model.onnx && \
    curl -sSL -o vocab.txt      https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main/vocab.txt && \
    curl -sSL -o config.json    https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main/config.json

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS final
WORKDIR /app

# Non-root. Container Apps does not require it; running as root anyway is a choice nobody
# should have to explain in a code review.
#
# `useradd`, not `adduser`: the runtime image is Ubuntu 24.04 with a minimal package set and
# ships only the former. Verified against the image — the adduser form fails the build.
RUN useradd --system --uid 1001 --no-create-home --shell /usr/sbin/nologin releaselens
USER 1001

COPY --from=build /app/publish .
COPY --from=model /model /app/models/bge-small-en-v1.5

ENV ASPNETCORE_HTTP_PORTS=8080 \
    ASPNETCORE_ENVIRONMENT=Production \
    DOTNET_gcServer=1

EXPOSE 8080

# No HEALTHCHECK, deliberately. The runtime image ships neither wget nor curl — verified
# against it — and installing a networking tool into a production image solely to probe our
# own endpoint is a poor trade. Liveness and readiness are probed over HTTP by the
# orchestrator instead: see liveness_probe and readiness_probe on the Container App in
# infra/terraform. /health serves both, and remains callable from the host during local runs.

ENTRYPOINT ["dotnet", "ReleaseLens.Api.dll"]
