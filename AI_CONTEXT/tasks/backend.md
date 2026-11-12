# Backend Task

Own `hiring-assessment-backend/`. Read all mandatory context first.

Implement FastAPI, SQLAlchemy, persistent database, import commands, candidate and HR APIs,
audio storage, idempotent versioned submissions, and DB-backed worker orchestration.

Important:
- Import 496 pipeline rows and 35 answer JSON records idempotently.
- Preserve all 35 source answer IDs and five audio files.
- Preserve A022 and A023 as separate versions of C2052/Q3; later A023 is current by import policy.
- Candidate response DTOs must never contain grades or HR-only fields.
- Same idempotency key + same payload returns existing submission; different payload returns 409.
- Do not create more than one scoring job for a given submission.
- Only current submissions are actively scored.
- Audio-only submissions remain visible to HR; without real transcription route them to needs_a_person.
- Implement safe worker claiming, retry, failure states and stale-claim recovery.
- No in-memory-only queue.

Do not modify `hiring-assessment-frontend/` or silently modify OpenAPI.
Write checkpoint and tests.
