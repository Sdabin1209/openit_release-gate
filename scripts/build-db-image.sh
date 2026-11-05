#!/usr/bin/env bash
# 영역 DB 이미지(release-gate-db) 빌드. build-image.sh가 요청의 variant가 db일 때 부른다(계약 BuildImageRequest.variant, D126).
# 입력: 요청 JSON 파일(BuildImageRequest). 결과: 표준 출력 마지막 줄에 BuildImageResult JSON 한 줄. 진행 기록은 표준 오류.
# 이미지에는 boundary-contracts·model-runtime·문진 스키마가 들어가지 않는다. 라벨 contracts.boundary는 이 이미지가 따르는 선언(DatabaseDeclaration)의 계약 판이고
# 이 리포의 고정(requirements.txt)과 요청이 같을 때만 찍는다(U50). survey-schema는 쓰지 않으므로 n/a.
set -euo pipefail
log() { echo "build-db-image: $*" >&2; }
die() { log "오류: $*"; exit 1; }
req="$1"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -n "${PYTHON:-}" ]; then PY="$PYTHON"; elif [ -x "$here/.venv/bin/python" ]; then PY="$here/.venv/bin/python"; else PY="python3"; fi
DOCKER="${DOCKER:-docker}"
mapfile -t F < <("$PY" - "$req" <<'PY'
import json, re, sys
from boundary_contracts import validator
r = json.load(open(sys.argv[1], encoding="utf-8"))
validator("BuildImageRequest").validate(r)
if r.get("variant") != "db":
    sys.exit("build-db-image: 오류: variant가 db가 아니다")
want = re.search(r"boundary-contracts\.git@(v[0-9][0-9.]*)", open(r["source_dir"] + "/requirements.txt", encoding="utf-8").read()).group(1)
if r["versions"]["boundary"] != want:
    sys.exit(f"build-db-image: 오류: 요청 versions.boundary({r['versions']['boundary']})가 이 리포의 고정({want})과 다르다. 라벨이 거짓이 되므로 빌드하지 않는다")
decl = json.load(open(r["source_dir"] + "/deploy-database.json", encoding="utf-8"))
validator("DatabaseDeclaration").validate(decl)
print(r["source_dir"], r["revision"], r["image_tag"], r["versions"]["boundary"],
      json.dumps(decl, ensure_ascii=False, separators=(",", ":"), sort_keys=True), sep="\n")
PY
) || exit 1
[ "${#F[@]}" -eq 5 ] || die "요청을 읽지 못했다"
src="${F[0]}" rev="${F[1]}" tag="${F[2]}" v_bc="${F[3]}" decl="${F[4]}"
[ -d "$src/db" ] && [ -f "$src/deploy-database.json" ] || die "source_dir에 db/ 와 deploy-database.json이 없다: $src"
eval "$("${PYTHON:-python3}" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker_env.py" --sh)"      # docker 환경 한 곳(scripts/docker_env.py): 사용자 ~/.docker에 기대지 않는다
"$DOCKER" info >/dev/null 2>&1 || die "도커 데몬에 연결할 수 없다('$DOCKER info' 실패)"
ctx="$(mktemp -d)"; trap 'rm -rf "$ctx"' EXIT
cp -a "$src/db/." "$ctx/"
cp -a "$src/deploy-database.json" "$ctx/database.json"
log "docker build: $tag"
"$DOCKER" build -q -t "$tag" -f "$ctx/Dockerfile" \
  --label "io.openit.database=$decl" --label "io.openit.contracts.boundary=$v_bc" --label "io.openit.contracts.survey-schema=n/a" --label "io.openit.variant=db" \
  --build-arg COMMIT="$rev" "$ctx" >&2
image_id="$("$DOCKER" image inspect -f '{{.Id}}' "$tag")"
labels="$("$DOCKER" image inspect -f '{{json .Config.Labels}}' "$tag")"
"$PY" - "$tag" "$image_id" "$labels" <<'PY'
import json, sys
from boundary_contracts import validator
tag, image_id, labels = sys.argv[1], sys.argv[2], json.loads(sys.argv[3] or "{}")
out = {"image": tag, "image_id": image_id, "labels": {k: v for k, v in labels.items() if k.startswith("io.openit.")}}
validator("ImageLabels").validate(out["labels"])        # 만드는 쪽 검증(R7): 이미지에 실제로 붙은 라벨이 계약 ImageLabels를 따른다
validator("BuildImageResult").validate(out)
print(json.dumps(out, ensure_ascii=False))
PY
