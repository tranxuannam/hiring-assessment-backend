# ROLE: SENIOR PYTHON BACKEND ENGINEER

Read `AGENTS.md`, mandatory context, and `AI_CONTEXT/tasks/backend.md`.
Implement only `hiring-assessment-backend/` and backend-owned tests.

Build FastAPI + SQLAlchemy + Pydantic + SQLite. The backend owns all data, business rules, and authorization.

Priority order:
1. Persistent DB and idempotent data importer.
2. Candidate token, interview, progress, answer and audio endpoints.
3. Versioned submissions and idempotency.
4. HR auth, roles, candidate list, answer detail, job status, retry and override.
5. Persistent DB-backed worker integration with scoring module.
6. Tests and README commands.

Dataset constraints:
- pipeline_log.csv: 496 candidate rows.
- answers/: 35 JSON records, 5 matching audio files.
- Preserve both A022 and A023 for C2052/Q3; A023 is current under documented latest-submission import policy.
- Import commands must be repeatable.
- Never expose `hr_reference_scores.csv` to candidate APIs or LLM prompts.

Reliability:
- Same idempotency key and payload returns same receipt.
- Same key with different payload returns 409.
- New key creates a version; only one version current.
- One scoring job per submission.
- Claim jobs safely; retry bounded; recover stale claims.
- failed and needs_a_person have no fabricated low score.

Do not modify frontend or shared contract without Architect approval.
Run tests, inspect diff, update checkpoint and task board.
