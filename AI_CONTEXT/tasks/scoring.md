# AI Scoring Specialist Task

Own `hiring-assessment-backend/app/scoring/` until integration.

Implement:
- Provider adapter that calls a real LLM API at runtime.
- Prompt built from exact question text and grading guide.
- Candidate answer explicitly treated as untrusted data.
- Structured output with score 1..4, non-empty reason, needs_a_person boolean, optional review_reason.
- Strict Pydantic validation; reject bool as integer score, floats, out-of-range values, missing fields and extra fields.
- No cached/fixed production scoring.
- Bounded retry for transient provider errors; worker owns job state transitions.
- Tests use fake provider client only.

Do not access reference scores to shape model answers.
Do not make a hiring decision or infer candidate suitability beyond the question rubric.
Provide a typed interface and checkpoint.
