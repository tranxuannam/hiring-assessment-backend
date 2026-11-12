# Backend Task Board

| ID | Task | Owner | Status | Evidence |
|---|---|---|---|---|
| DATA-01 | Idempotent import of pipeline, questions, answer versions, and audio | Backend | DONE | `tests/test_importer.py`: 496 candidates, 35 answers/jobs, five audio files, A023 current after rerun |
| BE-01 | FastAPI, SQLAlchemy, SQLite persistence and configuration | Backend | DONE | App initializes schema; editable install and `python -m pip check` pass |
| BE-02 | Candidate token, interview, progress, answer, and audio APIs | Backend | DONE | `tests/test_api.py`: token isolation, no HR data, text/audio receipt and ownership |
| BE-03 | Immutable versioning and idempotent submission receipts | Backend | DONE | Same-key replay, changed-payload conflict, new version and original-receipt replay tests |
| BE-04 | HR demo auth, candidate list, answer detail, job list/retry, override | Backend | DONE | Protected-route, answer discovery, audio download, retry, and override tests |
| AI-01 | Real configured LLM provider and strict structured output | Backend | DONE | OpenAI-compatible runtime client; missing key fails without a fake score |
| AI-02 | Untrusted-answer prompt and output validation | Backend | DONE | Strict schema tests reject bool/float/out-of-range/extra/malformed output |
| BE-05 | Persistent worker claims, bounded retries, stale-claim recovery | Backend | DONE | Worker tests cover exclusive claims, expired lease, score and failure states |
| QA-01 | Backend privacy, import, idempotency, audio, scoring tests | Backend | DONE | `python -m pytest`: 15 passed |
| QA-02 | Full frontend/backend contract and end-to-end integration | Integration | BLOCKED | Frontend is outside this repository/task; no shared contract file was changed |
| DOC-01 | Backend setup, run commands, lifecycle and architecture README | Backend | DONE | README updated with setup, importer/token, API, worker, and test commands |

The shared `hiring-assessment-specs/integration/task-board.md` was not edited because it
belongs to the separate shared-specs repository. Progress is recorded here and in the
session task tracker.
