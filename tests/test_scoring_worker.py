import json
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.auth import utcnow
from app.models import AnswerSubmission, Candidate, InterviewResponse, Question, Score, ScoringJob
from app.scoring import ScoringProviderError, score_answer
from app.scoring.service import InvalidScoringOutput
from app.worker import claim_next_job, process_claimed_job, run_once


class FakeProvider:
    model_name = "test-fake-provider"

    def complete(self, system_prompt, user_prompt):
        assert "untrusted data" in system_prompt
        assert "candidate_answer_untrusted" in user_prompt
        return json.dumps(
            {
                "score": 3,
                "reason": "The answer provides a concrete example and outcome.",
                "needs_a_person": False,
                "review_reason": None,
            }
        )


class FailingProvider:
    model_name = "test-fake-provider"

    def complete(self, _system_prompt, _user_prompt):
        raise ScoringProviderError("provider_unavailable", retryable=False)


class InvalidOutputProvider:
    model_name = "test-fake-provider"

    def __init__(self, value):
        self.value = value

    def complete(self, _system_prompt, _user_prompt):
        return self.value


class HumanReviewProvider:
    model_name = "test-fake-provider"

    def complete(self, _system_prompt, _user_prompt):
        return json.dumps(
            {
                "score": 1,
                "reason": "The answer cannot be assessed reliably.",
                "needs_a_person": True,
                "review_reason": "Human context is needed.",
            }
        )


def test_worker_claims_persists_score_and_rejects_candidate_access(backend):
    with backend.session_factory() as session:
        submission = session.scalar(
            select(AnswerSubmission)
            .join(InterviewResponse, AnswerSubmission.response_id == InterviewResponse.id)
            .join(Question, InterviewResponse.question_id == Question.id)
            .join(Candidate, AnswerSubmission.candidate_id == Candidate.id)
            .where(
                Candidate.external_id == "C2041",
                Question.external_id == "Q1",
                AnswerSubmission.is_current.is_(True),
            )
        )
        job_id = session.scalar(
            select(ScoringJob.id).where(ScoringJob.submission_id == submission.id)
        )

    assert run_once(backend.session_factory, FakeProvider()) is True
    with backend.session_factory() as session:
        job = session.get(ScoringJob, job_id)
        score = session.scalar(select(Score).where(Score.submission_id == submission.id))
        assert job.status == "scored"
        assert score.suggested_score == 3
        assert score.reason
        assert score.model_name == "test-fake-provider"
    interview = backend.client.get(
        "/api/v1/candidate/interview",
        headers={"Authorization": f"Bearer {backend.candidate_token}"},
    )
    assert "suggested_score" not in interview.text
    assert "grading_guide" not in interview.text


def test_failed_job_has_no_numeric_score(backend):
    with backend.session_factory() as session:
        submission = session.scalar(
            select(AnswerSubmission)
            .join(InterviewResponse, AnswerSubmission.response_id == InterviewResponse.id)
            .join(Question, InterviewResponse.question_id == Question.id)
            .join(Candidate, AnswerSubmission.candidate_id == Candidate.id)
            .where(
                Candidate.external_id == "C2041",
                Question.external_id == "Q1",
                AnswerSubmission.is_current.is_(True),
            )
        )
        job_id = session.scalar(
            select(ScoringJob.id).where(ScoringJob.submission_id == submission.id)
        )
    assert run_once(backend.session_factory, FailingProvider()) is True
    with backend.session_factory() as session:
        job = session.get(ScoringJob, job_id)
        assert job.status == "failed"
        assert session.scalar(select(Score).where(Score.submission_id == submission.id)) is None
        assert job.last_error_message != "provider_unavailable"


def test_human_review_outcome_does_not_store_numeric_score(backend):
    with backend.session_factory() as session:
        submission = session.scalar(
            select(AnswerSubmission)
            .join(InterviewResponse, AnswerSubmission.response_id == InterviewResponse.id)
            .join(Question, InterviewResponse.question_id == Question.id)
            .join(Candidate, AnswerSubmission.candidate_id == Candidate.id)
            .where(
                Candidate.external_id == "C2041",
                Question.external_id == "Q1",
                AnswerSubmission.is_current.is_(True),
            )
        )
        submission_id = submission.id
        job_id = session.scalar(
            select(ScoringJob.id).where(ScoringJob.submission_id == submission.id)
        )
        session.execute(
            update(ScoringJob)
            .where(ScoringJob.id != job_id, ScoringJob.status == "queued")
            .values(status="needs_a_person")
        )
        session.commit()
    assert run_once(backend.session_factory, HumanReviewProvider()) is True
    with backend.session_factory() as session:
        job = session.get(ScoringJob, job_id)
        score = session.scalar(select(Score).where(Score.submission_id == submission_id))
        assert job.status == "needs_a_person"
        assert score.suggested_score is None


def test_job_claim_is_exclusive_and_stale_claim_is_recovered(backend):
    with backend.session_factory() as session:
        submission = session.scalar(
            select(AnswerSubmission)
            .join(InterviewResponse, AnswerSubmission.response_id == InterviewResponse.id)
            .join(Question, InterviewResponse.question_id == Question.id)
            .join(Candidate, AnswerSubmission.candidate_id == Candidate.id)
            .where(
                Candidate.external_id == "C2041",
                Question.external_id == "Q1",
                AnswerSubmission.is_current.is_(True),
            )
        )
        job = session.scalar(select(ScoringJob).where(ScoringJob.submission_id == submission.id))
        job_id = job.id
        session.execute(
            update(ScoringJob)
            .where(ScoringJob.id != job_id, ScoringJob.status == "queued")
            .values(status="needs_a_person")
        )
        session.commit()
    first_claim = claim_next_job(backend.session_factory)
    assert first_claim is not None
    assert claim_next_job(backend.session_factory) is None
    process_claimed_job(backend.session_factory, *first_claim, provider=FakeProvider())

    with backend.session_factory() as session:
        job = session.get(ScoringJob, job_id)
        job.status = "queued"
        job.attempt_count = 1
        job.claim_token = "expired-worker"
        job.claimed_at = utcnow() - timedelta(minutes=5)
        job.lease_expires_at = utcnow() - timedelta(minutes=4)
        session.commit()
    recovered_claim = claim_next_job(backend.session_factory)
    assert recovered_claim is not None
    with backend.session_factory() as session:
        recovered = session.get(ScoringJob, job_id)
        assert recovered.attempt_count == 2
        assert recovered.claim_token == recovered_claim[1]


@pytest.mark.parametrize(
    "model_output",
    [
        '{"score": true, "reason": "bad", "needs_a_person": false, "review_reason": null}',
        '{"score": 2.5, "reason": "bad", "needs_a_person": false, "review_reason": null}',
        '{"score": 5, "reason": "bad", "needs_a_person": false, "review_reason": null}',
        '{"score": 2, "reason": " ", "needs_a_person": false, "review_reason": null}',
        '{"score": 2, "reason": "valid", "needs_a_person": false, "review_reason": null, "extra": 1}',
    ],
)
def test_scoring_rejects_invalid_provider_output(model_output):
    with pytest.raises(InvalidScoringOutput):
        score_answer("Question", "Guide", "Answer", InvalidOutputProvider(model_output))
