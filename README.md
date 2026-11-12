# Hiring Assessment Backend

FastAPI service backed by SQLAlchemy and SQLite. The backend owns authorization and
business rules. The shared OpenAPI 1.0.0 contract and snapshot fingerprint are recorded
in `SPECS_VERSION.md`; shared specs were not modified.

## Run locally

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
Copy-Item .env.example .env
python -m app.importer
```

To issue or rotate a candidate token, replace `C2041` with the candidate's ID and run:

```powershell
python -m app.importer --issue-token C2041
```

The command prints the new token only once and revokes that candidate's previous active
token. Share it through a private channel; only its SHA-256 hash is stored in the
database. Tokens expire after 180 days. Candidates send the token with API requests as
`Authorization: Bearer <token>`. Start the API and the persistent DB-backed worker in
separate terminals:

```powershell
python -m uvicorn app.main:app --reload
python -m app.worker
```

To create a candidate in an existing role and issue their first token, run:

```powershell
python -m app.importer --create-candidate 497 --name "Candidate Name" --email "candidate@example.com" --role "Backend Engineer"
```

The candidate ID must be unique and the role must already exist (run the importer first).
The command prints the raw token once; the database stores only its SHA-256 hash.

The HR demo login is visibly mocked: `hr-demo` / `change-me` unless changed in `.env`.
Change the demo credentials before exposing the service. Configure
`LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` for real runtime scoring. If no provider
key is configured, text-scoring jobs fail safely; there is no runtime fake-score
fallback. `.env` is loaded automatically and is ignored by Git.

Run backend tests with:

```powershell
python -m pytest
```

## Import behavior

`python -m app.importer` is repeatable and initializes the SQLite schema. It imports the
496 pipeline rows, six questions, all 35 answer records, and copies the five audio files
to the private `AUDIO_STORAGE_DIR`. The original files remain unchanged. `A022` and
`A023` are kept as separate versions of C2052/Q3; the latest `submitted_at` (A023) is
current. Re-running the importer does not duplicate records or overwrite a live
candidate submission. It never imports `hr_reference_scores.csv`.

## API and data lifecycle

Candidate endpoints under `/api/v1/candidate` validate a bearer token, return only the
candidate's questions/progress, and accept text or audio. `questions[].id` is the
candidate's logical response ID used by the audio upload path. `Idempotency-Key` plus
the canonical payload is unique per candidate: same key and payload replays its receipt;
same key with different content returns 409. A new key creates an immutable version and
updates the response's current pointer. Audio is stored outside public directories;
HR-only downloads require an authenticated cookie.

The DB stores roles, candidates, hashed candidate tokens, questions, logical response
slots, immutable submission versions, one scoring job per submission version,
score suggestions, HR sessions, and append-only HR reviews. The worker claims queued current text jobs with a database lease, retries transient provider failures with bounded
exponential backoff, and recovers expired claims. It only scores the current text
submission. Audio-only/empty answers and model-marked review cases become
`needs_a_person` without a numeric score; exhausted failures remain `failed`. HR
overrides require a reason and do not replace the AI suggestion.

Key contract routes: candidate interview/progress/answer/audio; HR mocked
auth/session, role candidates, answer detail/review, scoring-job list/retry. To make
the HR detail route navigable without changing the shared OpenAPI file, the backend
adds a `submissions` summary to each role-candidate item. The supporting HR audio route
is `/api/v1/hr/answers/{submission_id}/audio`. These additive backend routes/fields
were approved for this implementation; the shared contract remains untouched.

## Architecture and intentionally omitted features

SQLite is initialized reproducibly from SQLAlchemy model metadata. Filesystem audio
storage is private to the API process. A separate polling worker reads persistent job
rows and calls the configured OpenAI-compatible provider with the exact question, its
grading guide, and the answer as untrusted content. Pydantic strictly validates model
output. HR reference scores and other candidates' answers are never sent to the model.

Production HR identity, audio transcription, candidate ranking, automated hiring
decisions, and reference-score evaluation are intentionally omitted. The mocked HR
credentials and local SQLite/audio setup are for the demo only.
