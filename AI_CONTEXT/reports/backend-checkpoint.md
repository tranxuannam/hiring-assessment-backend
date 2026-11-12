# Backend Checkpoint

- **Task ID:** Backend implementation (DATA-01, BE-01 through BE-05, QA-01, DOC-01)
- **Agent:** Backend
- **Branch:** `main`
- **Contract version:** OpenAPI 1.0.0
- **Status:** Backend implementation complete; frontend/full-stack integration remains outside scope.

## Completed

- Added persistent SQLAlchemy/SQLite models and reproducible schema initialization.
- Added an idempotent importer for 496 pipeline rows, six questions, all 35 answer records, and five copied audio recordings. It preserves A022/A023 and selects A023 by latest submission timestamp. Every submission has one job; only current transcribed answers are queued.
- Added hashed candidate token authentication, candidate interview/progress/text/audio APIs, immutable versions, payload-bound idempotency, private audio storage, and HR-protected audio download.
- Added mocked HR cookie sessions, roles/candidates, navigable answer summaries, HR answer detail, job listing/retry, and append-only score overrides with reasons.
- Added a real OpenAI-compatible runtime scorer, untrusted-answer prompting, strict Pydantic output validation, persistent worker claims, bounded transient retries, and stale-lease recovery.
- Added setup/configuration documentation and backend-owned task board.
- Per user approval, added the backend-only HR audio download route and the `submissions` summary field on role-candidate results. The shared OpenAPI file was not modified.

## Files changed

- `app/`: configuration, persistence models, candidate/HR APIs, importer, scoring provider, and worker.
- `tests/`: API, importer, token, worker, and scoring validation coverage.
- `pyproject.toml`, `.env.example`, `.gitignore`, `README.md`, `SPECS_VERSION.md`.
- `AI_CONTEXT/tasks/backend-board.md`.

The existing untracked `AGENTS.md`, task prompts, and source data were preserved.

## Decisions and constraints

- Candidate response DTOs are explicit and exclude grading guides, scores, job errors, and other candidates.
- Audio without a real transcript is retained and marked `needs_a_person`.
- Failed jobs have no fabricated score; HR decisions remain separate from any AI suggestion.
- The default HR login is deliberately mocked and documented as demo-only.
- Shared specs are an uncommitted working tree with no available commit/tag. `SPECS_VERSION.md` records OpenAPI 1.0.0 and its SHA-256 snapshot fingerprint.
- The shared task board was not edited because it belongs to the separate specs repository; backend progress is mirrored locally and tracked in the session todo table.

## Tests and checks

- `python -m pytest` — **15 passed**.
- `python -m compileall -q app tests` — passed.
- `python -m pip install -e ".[test]"` — passed after dropping Uvicorn's optional standard extra, whose PyYAML source build required unavailable MSVC for this Python runtime.
- `python -m pip check` — no broken requirements.
- `git diff --check` — passed.
- `python -m uvicorn --version` — server CLI available.

Pytest emitted a Starlette/httpx deprecation warning and a Windows temporary-directory cleanup `PermissionError` at interpreter exit after reporting all tests passed.

## Blockers / next steps

- Full frontend/backend contract and user-journey integration must be run in the frontend/integration workstream.
- Shared-specs snapshot has no Git commit or tag to record; refresh `SPECS_VERSION.md` when a reviewed specs commit is available.
