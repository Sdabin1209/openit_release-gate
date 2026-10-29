# ADR-0016: 노출 선언 라벨과 어댑터 레이아웃 검증

- 상태: 채택
- 날짜: 2026-10-30

## 맥락

(1) deploy의 게이트웨이 설정(Caddyfile)이 이 서비스의 경로·메서드·역할을 직접 알았다(구조 규칙 1 위반, OPEN_GAPS U47). (2) 이웃(model-lab)에게서 받는 어댑터 디렉터리는 해시만 맞는지 봤고 파일 구성은 보지 않았다. (3) survey-service가 문항 세트만 필요한데 어댑터까지 내려받았다(SV-16).

## 결정

- 노출 표는 리포 안 `deploy-exposure.json`이 정본이고 `scripts/build-image.sh`가 계약 `Exposure`로 검증해 이미지 라벨 `io.openit.exposure`로 단다. 허용 경로는 deploy의 Caddyfile에서 이 서비스로 가는 블록을 옮겼다(승인 화면 읽기·결정, 운영자 읽기·변경). 열지 않는 경로(POST /v1/submissions, /v1/releases/*, /v1/decisions*)는 `auth.kind=denied`로 적는다. 새 경로 `GET /v1/releases/{id}/question-bank`도 denied다(survey-service가 내부 망에서 읽는다). 시험이 코드의 경로 문자열이 모두 표에 있는지, 허용한 경로가 코드에 있는지 본다. 어긋나면 코드가 정본이다.
- 이웃에게서 받은 어댑터는 승인 시점(`GateEngine._check_adapter_layout`)에 계약 `ModelPackageLayout`(필수 3파일, 그 밖의 파일 없음, 파일 해시)으로 확인하고 어긋나면 `INTEGRITY_ERROR`로 거부한다. 우리가 묶어 저장한 사본을 읽을 때는 다시 하지 않는다: 디렉터리 해시가 모든 파일을 덮어 변조·추가 파일이 해시에서 잡히고, 이미 우리가 검증해 저장한 값이기 때문이다(R7).
- 계약 2.7.0의 I4 `GET /v1/releases/{id}/question-bank`를 구현한다: PASS가 아니면 412, 없으면 404, 문항 세트 해시를 매니페스트와 대조한다. 어댑터는 읽지 않는다.

## 왜 / 대안

- 라벨 선언: deploy가 영역의 경로를 짐작하지 않고 이미지에서 읽는다(계약 Exposure 설명). 대안(deploy가 계속 안다)은 구조 규칙 1 위반이라 버린다.
- 레이아웃 검증을 읽기 쪽에도 두는 안: 해시와 같은 것을 두 번 보는 것이라 버린다.

## 결과 / 되돌리는 조건

얻는 것: 게이트웨이 설정과 이웃 입력 검증이 계약 스키마에 묶인다. 감수하는 것: 시험 어댑터가 계약 필수 3파일 모양이어야 하고, 노출 표와 코드를 함께 고쳐야 한다. 계약의 Exposure나 ModelPackageLayout이 바뀌면 이 ADR을 대체한다.
