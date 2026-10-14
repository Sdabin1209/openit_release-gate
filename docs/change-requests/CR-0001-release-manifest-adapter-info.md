# CR-0001 ReleaseManifest가 어댑터 해시와 serving_config를 담지 않는다

- 상태: 헤드 결정 대기. 작성: release-gate. 대상: `schemas/ReleaseManifest.schema.json`, `sdk.ReleaseBundle`.
- 문제: 서비스는 릴리스 하나로 모델을 올린다. I9 `load(adapters, serving_config, ..., expected_serving_config_hash)`에는 `serving_config`와 해시가 필요하다. 그런데 `ReleaseManifest`에는 LoRA의 ArtifactRef(`ModelPackageManifest`의 content_hash)만 있고 `adapter_hash`, `serving_config`, `serving_config_hash`가 없다. `ReleaseBundle`도 manifest, question_bank, adapters만 가진다. I3 없이 이 SDK 하나로 필요한 것을 얻는다는 I4의 약속(INTERFACES I4)이 지켜지지 않는다. 묶음의 어댑터 해시를 검증할 기준도 매니페스트에 없다.
- 임시 처리: 게이트가 `ext["release-gate"].adapters.{LORA_A,LORA_B} = {adapter_hash, serving_config, serving_config_hash}`에 넣고 `load_bundle`이 그것으로 검증한다. 이웃은 ext를 판단에 쓰면 안 되므로(CONVENTIONS 1절) 정식 필드가 필요하다.
- 제안(MINOR 추가): `ReleaseManifest.adapters`: `{LORA_A: {adapter_hash, serving_config, serving_config_hash}, LORA_B: {...}}`를 선택 필드로 추가하고 다음 MAJOR에서 필수로 올린다. `ReleaseBundle`에는 같은 정보를 `manifest`로 읽게 하면 충분하다.
