# 빌드 컨텍스트는 scripts/stage_context.sh 가 만든 .build/ctx 이다(계약 패키지와 문진 스키마를 함께 담는다).
# CI에서는 같은 구성을 Git 태그로 받아 채운다(D18). 직접 빌드: scripts/smoke.sh
FROM python:3.12-slim

ARG COMMIT=unknown
ARG CONTRACT_VERSION=unknown
ARG PIPELINE_SCHEMA_VERSION=unknown
ARG TURN_ENGINE_VERSION=unset
ARG MODEL_RUNTIME_VERSION=unset
LABEL org.opencontainers.image.revision="${COMMIT}" \
      org.openit.survey-contract-version="${CONTRACT_VERSION}" \
      org.openit.boundary-contract-version="${PIPELINE_SCHEMA_VERSION}" \
      org.openit.turn-engine-version="${TURN_ENGINE_VERSION}" \
      org.openit.model-runtime-version="${MODEL_RUNTIME_VERSION}"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    GATE_BOUNDARY_SCHEMAS_DIR=/opt/contracts/boundary/schemas \
    GATE_SURVEY_SCHEMA_DIR=/opt/contracts/survey-schema \
    GATE_DATA_DIR=/data \
    GATE_TURN_ENGINE_VERSION=${TURN_ENGINE_VERSION} \
    GATE_MODEL_RUNTIME_VERSION=${MODEL_RUNTIME_VERSION} \
    PORT=8080

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin gate \
 && mkdir -p /data /opt/contracts/boundary /opt/contracts/survey-schema && chown gate /data
WORKDIR /src
COPY release-gate/requirements.lock ./release-gate/requirements.lock
RUN pip install -r release-gate/requirements.lock
COPY boundary-contracts ./boundary-contracts
COPY survey-schema /opt/contracts/survey-schema
COPY release-gate ./release-gate
RUN pip install --no-deps ./boundary-contracts ./release-gate \
 && cp -r boundary-contracts/schemas /opt/contracts/boundary/schemas \
 && rm -rf /src

USER gate
WORKDIR /data
EXPOSE 8080
VOLUME /data
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8080'),timeout=2)"
CMD ["gate-service"]
