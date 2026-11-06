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
# 주입 선언(계약 ContainerInputs 3.5.0, 라벨 io.openit.inputs): 정본은 리포 안 JSON. 라벨로 달기 전에 계약으로 검증하고(R7), 빌드한 이미지의 실행 사용자와 대조한다.
# 왜 대조하는가: 배치 영역은 선언의 숫자 uid:gid 로 파일·쓰기 권한을 준다. 이미지와 다르면 기동에서야 권한 오류가 난다(2026-11-06 DB 비밀 읽기 실패).
inputs_label() {   # inputs_label <선언 파일> <이미지 이름> -> 한 줄 JSON(표준 출력)
  "${PYTHON:-${PY:-python3}}" -c '
import json, sys
from boundary_contracts import validator
d = json.load(open(sys.argv[1], encoding="utf-8"))
if d.get("image") != sys.argv[2]:
    sys.exit("%s 의 image(%s)가 이미지 이름 %s 와 다르다" % (sys.argv[1], d.get("image"), sys.argv[2]))
errs = list(validator("ContainerInputs").iter_errors(d))
if errs:
    sys.exit("%s 가 ContainerInputs 를 어긴다: " % sys.argv[1] + ", ".join("/" + "/".join(map(str, e.absolute_path)) + ":" + e.validator for e in errs[:5]))
print(json.dumps(d, ensure_ascii=False, separators=(",", ":"), sort_keys=True))' "$1" "$2"
}
inputs_check() {   # inputs_check <이미지 태그> <inputs_label 출력>: 다르면 이미지를 지우고 실패
  local u g cu
  # 실행 사용자: 이미지 안 id 로 읽는다. id 가 없는 이미지(distroless)는 이미지 설정 User 가 숫자 uid:gid 일 때만 그 값을 쓴다(이름이면 판정하지 못해 실패)
  if u=$("${DOCKER:-docker}" run --rm --network none --entrypoint id "$1" -u 2>/dev/null) && g=$("${DOCKER:-docker}" run --rm --network none --entrypoint id "$1" -g 2>/dev/null); then :
  else
    cu=$("${DOCKER:-docker}" image inspect "$1" --format '{{.Config.User}}' 2>/dev/null)
    if [[ "$cu" =~ ^([0-9]+):([0-9]+)$ ]]; then u=${BASH_REMATCH[1]}; g=${BASH_REMATCH[2]}
    else "${DOCKER:-docker}" rmi -f "$1" >/dev/null 2>&1; echo "build-image: 이미지 실행 사용자를 읽지 못했다(id 없음, User=$cu): $1" >&2; return 1; fi
  fi
  "${PYTHON:-${PY:-python3}}" -c '
import json, sys
d = json.loads(sys.argv[1]); u, g = int(sys.argv[2]), int(sys.argv[3])
bad = sorted(k for s in ("roles", "jobs") for k, st in d[s].items() if (st["process"]["uid"], st["process"]["gid"]) != (u, g))
sys.exit("주입 선언의 process 가 이미지 실행 사용자 %d:%d 와 다르다: %s" % (u, g, bad) if bad else 0)' "$2" "$u" "$g" \
    || { "${DOCKER:-docker}" rmi -f "$1" >/dev/null 2>&1; echo "build-image: 주입 선언이 이미지와 다르다. 이미지를 지웠다: $1" >&2; return 1; }
}
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
inputs="$(inputs_label "$here/db/inputs.json" release-gate-db)" || die "db/inputs.json 이 계약 ContainerInputs 를 어긴다"
"$DOCKER" build -q -t "$tag" -f "$ctx/Dockerfile" \
  --label "io.openit.database=$decl" --label "io.openit.inputs=$inputs" --label "io.openit.contracts.boundary=$v_bc" --label "io.openit.contracts.survey-schema=n/a" --label "io.openit.variant=db" \
  --build-arg COMMIT="$rev" "$ctx" >&2
inputs_check "$tag" "$inputs" || exit 1
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
