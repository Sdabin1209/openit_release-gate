# 빌드 컨텍스트는 scripts/stage_context.sh 가 만든 .build/ctx 이다: boundary-contracts 태그 시점 소스로 만든
# wheel(wheels/), 선택으로 model-runtime wheel, 문진 스키마. 이미지 안에서는 ssh로 git을 받지 않는다. 직접 빌드: scripts/smoke.sh
FROM python:3.12-slim

# 왜 n/a: 값 없음의 표기는 계약 ImageLabels에서 n/a 하나다(none·unset·unknown 불가). build_image.sh는 항상 실제 값을 넘긴다.
ARG COMMIT=n/a
ARG BOUNDARY_VERSION=n/a
ARG SURVEY_SCHEMA_VERSION=n/a
ARG MODEL_RUNTIME_VERSION=n/a
LABEL io.openit.service="release-gate" \
      io.openit.revision="${COMMIT}" \
      io.openit.contracts.boundary="${BOUNDARY_VERSION}" \
      io.openit.contracts.survey-schema="${SURVEY_SCHEMA_VERSION}" \
      io.openit.model-runtime="${MODEL_RUNTIME_VERSION}"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    GATE_SURVEY_SCHEMA_DIR=/opt/contracts/survey-schema \
    GATE_DATA_DIR=/data \
    GATE_SUPPORTED_RUNTIME_CONTRACT_VERSION=${SURVEY_SCHEMA_VERSION} \
    PORT=8080

RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin gate \
 && mkdir -p /data /opt/contracts/survey-schema && chown gate /data
WORKDIR /src
COPY release-gate/requirements.lock ./release-gate/requirements.lock
RUN pip install -r release-gate/requirements.lock
COPY wheels ./wheels
COPY survey-schema /opt/contracts/survey-schema
COPY release-gate ./release-gate
# model-runtime wheel이 컨텍스트에 있으면 함께 설치한다. 서비스는 설치된 버전을 읽어 매니페스트에 쓴다.
RUN pip install --no-deps ./wheels/*.whl ./release-gate && rm -rf /src

USER gate
WORKDIR /data
EXPOSE 8080
VOLUME /data
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8080'),timeout=2)"
CMD ["python", "-m", "release_gate", "serve"]
