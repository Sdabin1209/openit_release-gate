# release-gate-db: 이 영역의 데이터베이스 컴포넌트(계약 3.4.10 D126). 공식 postgres 이미지 위에 영역이 정한 계정·권한·접속 규칙·TLS를 이미지 안에 둔다.
# 입력은 계약 AreaDatabaseEnv(POSTGRES_PASSWORD_FILE, DB_ACCOUNT_SECRETS_DIR, DB_TLS_DIR)뿐이고 deploy는 선언(라벨 io.openit.database)대로 비밀을 만들어 배치만 한다.
# 빌드 컨텍스트: scripts/build-image.sh(variant db)가 db/ 의 파일과 deploy-database.json(→ database.json)을 한 폴더에 모은다.
# 기반 이미지는 digest로 고정한다(공통 규칙 '빌드 때 고정'): 태그만 쓰면 받는 날마다 다른 이미지가 들어가 같은 소스가 다른 이미지가 된다. 값은 2026-11-05에 이 PC에 있던 같은 태그의 RepoDigest다.
# 올리는 방법: `docker pull <태그>` 뒤 `docker image inspect <태그> --format '{{index .RepoDigests 0}}'`의 값으로 아래 ARG 기본값만 바꾸고, 이미지 빌드 시험과 스모크를 다시 돌린다.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
ARG POSTGRES_IMAGE=postgres:16@sha256:1beaa3084eee2676f20a9cd9928f0bf2c5bf674f8e49f81017912183d377c61c
FROM ${PYTHON_IMAGE} AS gen
COPY database.json gen.py /work/
# 왜 빌드 때 만든다: 선언(JSON)이 정본이고 이미지 안 셸 초기화가 JSON을 파싱하지 않게, 계정 표(TSV)와 pg_hba.conf를 선언에서 파생한다
RUN python /work/gen.py /work/database.json /out

FROM ${POSTGRES_IMAGE}
ARG COMMIT=n/a
LABEL io.openit.service="release-gate-db" io.openit.revision="${COMMIT}"
COPY --from=gen /out/accounts.tsv /out/pg_hba.conf /out/port /etc/openit/
COPY docker-entrypoint-openit.sh /usr/local/bin/docker-entrypoint-openit.sh
COPY init-openit.sh /docker-entrypoint-initdb.d/10-openit-init.sh
# 왜 scram: initdb가 만드는 데이터 폴더의 pg_hba.conf에도 trust가 남지 않게 한다(실제 접속 규칙은 /etc/openit/pg_hba.conf)
ENV POSTGRES_INITDB_ARGS="--auth-local=scram-sha-256 --auth-host=scram-sha-256"
HEALTHCHECK --interval=10s --timeout=3s --retries=12 CMD pg_isready -q
ENTRYPOINT ["/usr/local/bin/docker-entrypoint-openit.sh"]
CMD ["postgres"]
