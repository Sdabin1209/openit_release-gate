# 시험과 시간 (U422, ADR-0019)

시험 중 실제 시간을 기다리거나 재서 판정하는 곳은 0이다. 정적 확인: `tests/test_no_real_time.py`(시험 코드의 `time.monotonic/perf_counter/time.time/time.sleep/datetime.now`와 숫자 timeout 0, 컴포넌트 코드의 시계 직접 읽기 0).
남은 대기는 시험이 자기 가짜에게서 신호를 기다리는 것뿐이고(`started.wait(...)`, 클라이언트 `timeout=`), 마감은 `tests/conftest.py`의 `HANG_GUARD_SECONDS` 하나다(매달림 감시: 정상이면 즉시 끝나고 코드가 진짜로 멈췄을 때만 걸린다. 판정이 아니다).

처리 분류: 1 = 우리 코드의 시간(시계 주입, 가짜 시계로 시간을 직접 흘림), 2 = 라이브러리 안의 시간(넘긴 값 확인 + 가짜 의존이 즉시 오류, 실제 대기 시험은 지움), 3 = 속도 측정(시간 단언 삭제), 매달림 감시, 지움.
실제 서버·DB를 얼리거나 느린 소켓으로 끝까지 맞물리는지는 영역 시험이 아니라 deploy smoke·E2E의 몫이다(아래 "지운 시험 중 smoke·E2E에서 볼 것").

| 파일 | 시험 | 목적 | 처리 | 바뀐 시험 |
| --- | --- | --- | --- | --- |
| test_audit_fixes.py | test_bundle_copy_does_not_hold_the_store_lock | 큰 복사 중에도 읽기가 저장소 잠금에 막히지 않는다 | 매달림 감시 | test_bundle_copy_does_not_hold_the_store_lock (sleep과 지연 상한 단언 대신, 복사를 Event로 멈춰 둔 채 읽기가 끝나는지 본다) |
| test_audit_fixes.py | test_slow_client_is_cut_by_request_timeout | 느린 연결을 요청 시한에 끊는다(waitress의 일) | 지움 (2) | test_waitress_gets_the_configured_request_timeout (test_u4a_waitress.py) |
| test_audit_fixes.py | test_graceful_stop_waits_for_inflight_and_refuses_new | 우아한 종료는 처리 중인 요청을 기다리고 새 연결을 거절한다 | 1 | test_graceful_stop_waits_for_inflight_and_refuses_new (가짜 시계, 첫 sleep에서 새 연결 거절 확인과 요청 해제) |
| test_audit_fixes.py | test_graceful_stop_returns_remaining_after_grace | 유예가 지나도 끝나지 않은 요청 수를 돌려준다 | 1 | test_graceful_stop_returns_remaining_after_grace (가짜 시간이 유예만큼 흘렀는지 아래쪽 경계) |
| test_audit_fixes.py | test_serve_exits_cleanly_on_sigterm | SIGTERM에 우아하게 끝난다(실제 프로세스·신호·시계) | 지움 (2: OS 신호) | test_serve_shutdown_sequence_on_sigterm (등록된 핸들러를 직접 불러 종료 절차의 순서와 인자를 확인) |
| test_audit_fixes.py | test_loop_is_joined_before_store_close | 진행 중인 주기 작업이 끝난 뒤에 저장소를 닫는다 | 매달림 감시 | test_loop_is_joined_before_store_close (작업이 종료 요청을 기다리는 Event로 순서를 만든다) |
| test_audit_fixes.py | test_concurrent_prune_and_activate_never_leave_pointer_on_pruned_release | 정리와 활성화가 겹쳐도 포인터가 정리된 릴리스를 가리키지 않는다 | 매달림 감시 | test_concurrent_prune_and_activate_never_leave_pointer_on_pruned_release (sleep 대신 세마포어로 번갈아 실행) |
| test_audit_fixes.py | test_purge_stale_tmp_keeps_dirs_with_recent_writes_inside | 최근에 쓰인 임시 디렉터리는 지우지 않는다 | 1 | test_purge_stale_tmp_keeps_dirs_with_recent_writes_inside (주입한 now, 파일 시각을 직접 정함) |
| test_audit_fixes.py | (retention 시험들) | 보존 기간 판단 | 1 | now는 conftest의 NOW 상수로 주입(실제 시계를 읽지 않는다) |
| test_pg_timeouts.py | test_startup_against_unresponsive_server_ends_within_connect_timeout | 응답 없는 서버에 시작이 연결 시한 안에 끝난다(psycopg의 일) | 지움 (2) | test_startup_connect_failure_is_unavailable_and_leaks_nothing, test_connection_params_carry_connect_and_statement_timeouts |
| test_pg_timeouts.py | test_jobs_end_nonzero_when_db_unresponsive | DB가 안 열리면 잡이 재시도 가능 종료 코드로 끝난다 | 지움 (2) | test_jobs_end_nonzero_when_db_is_unreachable (연결이 즉시 실패) |
| test_pg_timeouts.py | test_readyz_does_not_wait_for_a_stuck_status_read | 보조 수치 읽기가 막혀도 /readyz는 마지막 값으로 답한다 | 1 | test_readyz_does_not_wait_for_a_stuck_status_read (가짜 시계의 join이 시간 초과로 끝난다) |
| test_pg_timeouts.py | test_server_side_statement_timeout_is_set | 서버가 문장을 시한에 취소한다(PostgreSQL의 일) | 2 | test_server_side_statement_timeout_is_set (SHOW로 값만 확인) |
| test_pg_timeouts.py | test_readyz_not_blocked_by_main_connection_lock | /readyz가 주 연결 잠금에 막히지 않는다 | 매달림 감시 | test_readyz_not_blocked_by_main_connection_lock |
| test_pg_timeouts.py | test_readyz_503_within_limit_on_paused_db_and_recovers | DB가 얼면 /readyz는 503, 풀리면 200 | 지움 (2: docker pause) | test_readyz_503_when_the_probe_fails_and_recovers, test_ping_that_never_finishes_is_unavailable |
| test_pg_timeouts.py | test_request_path_ends_within_limit_on_paused_db_and_recovers | DB 장애에 읽기·쓰기 경로가 UNAVAILABLE(재시도 가능)이고 회복한다 | 지움 (2: docker pause) | test_request_path_maps_db_failures_to_retryable_unavailable_and_reconnects, test_abort_unblocks_a_stuck_read_on_the_connection_socket, test_armed_watch_arms_a_timer_of_limit_plus_one_and_cancels_it_when_the_statement_finishes, test_store_hands_its_scheduler_to_every_statement_guard |
| test_pg_timeouts.py | test_collect_job_ends_nonzero_when_db_paused | 수집 잡이 DB 장애에 비정상 종료 | 지움 (2) | test_jobs_end_nonzero_when_db_is_unreachable |
| test_pg_timeouts.py | test_activate_and_rollback_with_paused_db_end_503_and_keep_active_release | DB 장애 중 activate는 UNAVAILABLE이고 포인터는 그대로 | 지움 (2) | test_activate_with_failing_db_is_unavailable_and_keeps_the_active_release |
| test_pg_timeouts.py | (설정 전달) | 올바른 값을 넘겼는가 | 2 | test_registry_helper_passes_the_timeouts_to_the_store, test_main_passes_the_env_timeouts_to_open_registry, test_timeout_settings_are_validated, test_timeout_defaults_and_override |
| test_u4a_waitress.py | test_slow_client_does_not_block_other_requests | 느린 연결이 다른 요청을 막지 않는다(waitress의 일) | 지움 (2) | test_waitress_gets_the_connection_cap_threads_and_body_limit |
| test_u4a_waitress.py | test_idle_connection_is_closed_by_timeout | 유휴 연결을 시한에 끊는다(waitress의 일) | 지움 (2) | test_waitress_gets_the_configured_request_timeout |
| test_u4a_waitress.py | test_server_recovers_after_connections_exceed_the_cap | 연결 상한을 넘겨도 회복한다(waitress의 일) | 지움 (2) | test_waitress_gets_the_connection_cap_threads_and_body_limit |
| test_u4a_waitress.py | test_oversized_body_is_refused_with_4xx | 1MiB 넘는 본문을 거절한다(waitress의 일, 소켓 경쟁으로 흔들렸다) | 지움 (2) | test_waitress_gets_the_connection_cap_threads_and_body_limit, test_link_library_body_limit_matches_the_waitress_limit |
| test_u4a_waitress.py | test_graceful_stop_lets_the_inflight_request_finish_and_returns | 우아한 종료가 처리 중인 요청을 마치고 돌려준다 | 1 | test_graceful_stop_lets_the_inflight_request_finish_and_returns (가짜 시계) |
| test_upstream_faults.py | test_approval_with_upstream_fault_records_nothing_and_can_be_retried | 승인 중 이웃 장애(시간 초과 포함)에 아무것도 기록하지 않고 다시 보낼 수 있다 | 2 | test_approval_with_upstream_fault_records_nothing_and_can_be_retried (hang은 즉시 UNAVAILABLE을 던지는 가짜 이웃) |
| test_upstream_faults.py | test_collect_job_defers_on_upstream_fault | 수집 잡이 이웃 장애에 미룬다(75) | 2 | test_collect_job_defers_on_upstream_fault (hang·slow는 가짜 이웃) |
| test_upstream_faults.py | test_loop_task_defers_on_upstream_fault_without_hanging_or_recording | 첫 일시 오류에서 이번 주기를 멈춘다 | 3 | test_loop_task_defers_on_upstream_fault_without_hanging_or_recording (시간 대신 이웃이 받은 요청 수) |
| test_upstream_faults.py | (읽기 시한 값) | 이웃 클라이언트에 시한 값을 맞게 넘긴다 | 2 | test_factories_pass_the_read_timeout_to_the_clients |
| test_http_clients.py | (클라이언트 인자 timeout) | 로컬 서버 한 번 왕복 | 매달림 감시 | test_gate_service_over_http, test_reader_over_http 등 (HANG_GUARD_SECONDS) |
| test_http_integration.py | (클라이언트 인자 timeout) | 로컬 서버 한 번 왕복 | 매달림 감시 | test_factories_build_real_clients 등 (HANG_GUARD_SECONDS) |
| test_u47.py | (클라이언트 인자 timeout) | 로컬 서버 한 번 왕복 | 매달림 감시 | test_question_bank_over_http_roundtrips_with_the_contract_client 등 (HANG_GUARD_SECONDS) |
| test_postgres.py | (urlopen timeout, 스레드 합류) | 로컬 서버 한 번 왕복, 동시 실행 합류 | 매달림 감시 | HANG_GUARD_SECONDS |
| test_conformance.py | test_pointer_compare_and_swap_is_atomic | 동시에 같은 기대 버전으로 바꾸면 하나만 성공한다 | 매달림 감시 | barrier.wait, join에 HANG_GUARD_SECONDS |
| test_u43_setup_env_report.py | (git ls-remote, setup-env 실행) | 설치 스크립트가 보고 JSON을 낸다 | 매달림 감시 | subprocess timeout에 HANG_GUARD_SECONDS |

## 지운 시험 중 smoke·E2E에서 볼 것 (헤드 판단)
- 실제 waitress가 느린 연결을 끊고 큰 본문을 413으로 거절하며 연결 상한 뒤 회복하는지: 값은 넘기는 것까지만 확인한다. 실제 동작은 smoke에서 큰 본문 한 번(413)이면 충분하다.
- DB가 얼었을 때 /readyz 503과 요청 경로 UNAVAILABLE이 상한 안에 나오는지, 풀린 뒤 회복하는지: docker pause를 쓰는 deploy 쪽 시험의 몫이다.
- SIGTERM으로 컨테이너가 우아하게 끝나는지(종료 코드 0): deploy의 stop_grace_period 점검의 몫이다.
