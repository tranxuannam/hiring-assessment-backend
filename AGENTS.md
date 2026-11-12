# Backend Agent Instructions

You own this repository only. Shared product/architecture/business context is in the sibling
`../hiring-assessment-specs` repository. Read its relevant documents before coding.

## Required reading
- `../hiring-assessment-specs/requirements/product-requirements.md`
- `../hiring-assessment-specs/architecture/system-architecture.md`
- `../hiring-assessment-specs/architecture/database-design.md`
- `../hiring-assessment-specs/business-rules/scoring-and-submission-rules.md`
- `../hiring-assessment-specs/architecture/security.md`
- `../hiring-assessment-specs/contracts/openapi.yaml`
- `../hiring-assessment-specs/integration/frontend-backend.md`

## Scope
Implement FastAPI, persistence, migrations, import, authorization, candidate/HR APIs,
submission versioning/idempotency, background jobs, audio storage/access, LLM scoring and tests.

## Non-negotiable rules
- Backend is the authorization and business-rule source of truth.
- Never expose HR-only data or reference scores to candidate APIs or LLM prompts.
- Do not score a failed job as a low score; preserve `failed` and `needs_a_person` states.
- Preserve multiple submissions; do not put a unique constraint on `(candidate_id, question_id)`
  that destroys answer history.
- A duplicate request with the same idempotency key and same payload returns the original result;
  same key with different payload returns conflict.
- Store candidate access tokens as hashes and never log raw tokens.
- Audio-only submissions must be retained and surfaced to HR; do not fabricate transcripts.
- HR override requires a reason and preserves the AI suggestion separately.
- AI score is advisory; HR makes the final decision.
- Runtime scoring must use a real LLM provider; fake provider is for tests only.

## Change discipline
Do not change API shape or shared business rules unilaterally. Propose the change in specs first,
then implement the reviewed contract. Record the specs tag/commit this repo consumes.
