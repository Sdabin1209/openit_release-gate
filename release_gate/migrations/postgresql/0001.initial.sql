-- 왜 IF NOT EXISTS: 마이그레이션 도입 전 코드가 시작 때 같은 DDL을 실행해 만든 DB(이력 표가 없다)를 그대로 받아들인다. 이미 있으면 아무것도 바꾸지 않고 이력만 남는다.
-- 이후 스키마 변경은 이 파일을 고치지 않고 새 파일(0002.이름.sql 이고 첫머리에 depends 표시로 0001.initial을 가리킨다)로 더한다(docs/adr/0014-yoyo-migrations.md).
CREATE TABLE IF NOT EXISTS submissions(seq BIGSERIAL PRIMARY KEY, submission_id TEXT UNIQUE NOT NULL,
       subject_key TEXT UNIQUE NOT NULL, record TEXT NOT NULL, work TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(seq BIGSERIAL PRIMARY KEY, decision_id TEXT UNIQUE NOT NULL,
       submission_id TEXT NOT NULL, qs TEXT NOT NULL, la TEXT NOT NULL, lb TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS releases(seq BIGSERIAL PRIMARY KEY, release_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pointer_history(pointer_version BIGINT PRIMARY KEY, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(submission_id TEXT PRIMARY KEY, approval_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts(request_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, record TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(seq BIGSERIAL PRIMARY KEY, at TEXT NOT NULL, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_submissions_state ON submissions(state, seq);
CREATE INDEX IF NOT EXISTS idx_decisions_submission ON decisions(submission_id, seq);
