#!/usr/bin/env bash
# 왜: 영역 환경 준비의 정본은 이 파일 하나다. 새 머신으로 옮길 때 README마다 설치법이 달라(이웃 editable, 라이브러리 누락) 깨졌기 때문이다.
# 설치는 이 리포의 핀 파일(requirements.txt, requirements.lock, requirements-libs.txt)로만 한다. 버전 숫자는 여기에 적지 않는다.
# 결과: 표준 출력 마지막 줄에 계약 SetupEnvReport JSON 한 줄(boundary-contracts 2.6.0, U43). 진행 기록은 모두 표준 오류로 낸다.
# 왜 이 줄: deploy가 이 영역의 .venv 경로와 시험 명령을 짐작하지 않고 이 선언만 읽게 한다(구조 규칙 1). 멱등, 인자 없음, 비밀 없음.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # 어느 폴더에서 실행해도 리포 루트 기준
start=$SECONDS
# 왜 printf로 직접 만든다: 실패해서 .venv가 없을 때도 보고를 내야 해서 파이썬에 기대지 않는다. 값은 모두 상수다.
emit() { printf '{"area":"release-gate","status":"%s","exit_code":%d,"seconds":%d,"environment":{"kind":"python-venv","path":".venv","test_command":[".venv/bin/python","-m","pytest","-q"]}}\n' "$1" "$2" "$((SECONDS - start))"; }
trap 'rc=$?; if [ "$rc" -eq 0 ]; then emit ok 0; else emit fail "$rc"; fi' EXIT

[ -d .venv ] || python3 -m venv .venv >&2
PIP=(.venv/bin/python -m pip install -q --disable-pip-version-check)

# 왜 한 번 재시도: 일시적 네트워크 실패(git 접속, 패키지 다운로드)는 흔하고 다시 하면 대개 풀린다. 두 번 실패하면 이유를 내고 멈춘다.
run() {
  local what="$1"; shift
  if "$@"; then return 0; fi
  echo "setup-env: '$what' 실패, 한 번 재시도한다" >&2
  if "$@"; then return 0; fi
  echo "setup-env: '$what' 실패(재시도도 실패). 네트워크, GitHub SSH 접근(git+ssh), 핀 파일을 확인한다." >&2
  exit 1
}

# --upgrade: 오래된 설치가 있어도 핀 버전과 같아지게 한다(잠금 파일은 == 핀이라 내려가기도 한다).
run "계약 패키지와 잠금 의존 설치" "${PIP[@]}" --upgrade -r requirements.txt >&2
# model-runtime: 태그 핀(requirements-libs.txt), 의존은 위에서 이미 맞췄으므로 --no-deps. 같은 버전이어도 소스가 다를 수 있어 다시 설치한다.
run "model-runtime 설치" "${PIP[@]}" --no-deps --force-reinstall -r requirements-libs.txt >&2
run "이 리포와 시험 도구 설치" "${PIP[@]}" -e '.[test]' >&2

.venv/bin/python -m pip check >&2 || { echo "setup-env: 의존 충돌(pip check 실패)" >&2; exit 1; }
echo "setup-env: 완료" >&2
.venv/bin/python -m pip list 2>/dev/null | grep -iE '^(boundary-contracts|model-runtime|release-gate|pytest) ' >&2 || true
