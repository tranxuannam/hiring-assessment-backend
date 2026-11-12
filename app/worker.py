import argparse
import time
import uuid
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.auth import utcnow
from app.config import settings
from app.database import SessionLocal, initialize_database
from app.models import AnswerSubmission, InterviewResponse, Question, Score, ScoringJob
from app.scoring import ScoringProvider, ScoringProviderError, score_answer
from app.scoring.service import InvalidScoringOutput


def recover_stale_claims(session: Session) -> int:
    now = utcnow()
    stale = session.scalars(
        select(ScoringJob).where(
            ScoringJob.status == "queued",
            ScoringJob.claimed_at.is_not(None),
            ScoringJob.lease_expires_at <= now,
        )
    ).all()
    recovered = 0
    for job in stale:
        job.claimed_at = None
        job.lease_expires_at = None
        job.claim_token = None
        job.updated_at = now
        if job.attempt_count >= job.max_attempts:
            job.status = "failed"
            job.last_error_code = "worker_lease_expired"
            job.last_error_message = "Scoring worker stopped before completing the job."
        else:
            job.next_attempt_at = now
            job.last_error_code = "worker_lease_expired"
            job.last_error_message = "Scoring worker claim expired; the job was requeued."
        recovered += 1
    session.commit()
    return recovered


def claim_next_job(session_factory: sessionmaker) -> tuple[int, str] | None:
    with session_factory() as session:
        recover_stale_claims(session)
        now = utcnow()
        eligible = session.scalar(
            select(ScoringJob.id)
            .join(AnswerSubmission, ScoringJob.submission_id == AnswerSubmission.id)
            .join(InterviewResponse, AnswerSubmission.response_id == InterviewResponse.id)
            .where(
                ScoringJob.status == "queued",
                ScoringJob.next_attempt_at <= now,
                InterviewResponse.current_submission_id == AnswerSubmission.id,
                or_(
                    ScoringJob.claimed_at.is_(None),
                    ScoringJob.lease_expires_at <= now,
                ),
            )
            .order_by(ScoringJob.next_attempt_at, ScoringJob.id)
        )
        if eligible is None:
            return None
        claim_token = str(uuid.uuid4())
        result = session.execute(
            update(ScoringJob)
            .where(
                ScoringJob.id == eligible,
                ScoringJob.status == "queued",
                or_(
                    ScoringJob.claimed_at.is_(None),
                    ScoringJob.lease_expires_at <= now,
                ),
            )
            .values(
                attempt_count=ScoringJob.attempt_count + 1,
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=settings.worker_lease_seconds),
                claim_token=claim_token,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            session.rollback()
            return None
        session.commit()
        return eligible, claim_token


def _finish_failure(
    session_factory: sessionmaker,
    job_id: int,
    claim_token: str,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    with session_factory() as session:
        job = session.get(ScoringJob, job_id)
        if job is None or job.claim_token != claim_token:
            return
        now = utcnow()
        can_retry = retryable and job.attempt_count < job.max_attempts
        job.status = "queued" if can_retry else "failed"
        job.next_attempt_at = (
            now + timedelta(seconds=min(60, 2 ** max(0, job.attempt_count - 1)))
            if can_retry
            else None
        )
        job.claimed_at = None
        job.lease_expires_at = None
        job.claim_token = None
        job.last_error_code = code[:80]
        job.last_error_message = message[:500]
        job.updated_at = now
        session.commit()


def process_claimed_job(
    session_factory: sessionmaker,
    job_id: int,
    claim_token: str,
    provider: ScoringProvider | None = None,
) -> None:
    with session_factory() as session:
        job = session.get(ScoringJob, job_id)
        if job is None or job.claim_token != claim_token:
            return
        submission = session.get(AnswerSubmission, job.submission_id)
        response = session.get(InterviewResponse, submission.response_id)
        if response.current_submission_id != submission.id:
            job.status = "needs_a_person"
            job.last_error_code = "superseded_submission"
            job.last_error_message = "This version was replaced before scoring."
            job.claimed_at = job.lease_expires_at = None
            job.claim_token = None
            job.updated_at = utcnow()
            session.commit()
            return
        if not submission.answer_text or not submission.answer_text.strip():
            job.status = "needs_a_person"
            job.last_error_code = "empty_answer"
            job.last_error_message = "No transcript is available; manual review is required."
            job.claimed_at = job.lease_expires_at = None
            job.claim_token = None
            job.updated_at = utcnow()
            session.commit()
            return
        question = session.get(Question, response.question_id)
        question_text = question.question_text
        grading_guide = question.grading_guide
        answer_text = submission.answer_text

    try:
        result, model_name = score_answer(
            question_text, grading_guide, answer_text, provider=provider
        )
    except ScoringProviderError as error:
        _finish_failure(
            session_factory,
            job_id,
            claim_token,
            error.code,
            "The scoring provider could not complete this request.",
            error.retryable,
        )
        return
    except InvalidScoringOutput:
        _finish_failure(
            session_factory,
            job_id,
            claim_token,
            "invalid_model_output",
            "The scoring provider returned an invalid response.",
            False,
        )
        return

    with session_factory() as session:
        job = session.get(ScoringJob, job_id)
        if job is None or job.claim_token != claim_token:
            return
        submission = session.get(AnswerSubmission, job.submission_id)
        response = session.get(InterviewResponse, submission.response_id)
        if response.current_submission_id != submission.id:
            job.status = "needs_a_person"
            job.last_error_code = "superseded_submission"
            job.last_error_message = "This version was replaced while scoring."
        elif result.needs_a_person:
            job.status = "needs_a_person"
            job.last_error_code = "human_review_required"
            job.last_error_message = (
                result.review_reason or "A human reviewer should assess this answer."
            )[:500]
            session.add(
                Score(
                    job_id=job.id,
                    submission_id=submission.id,
                    suggested_score=None,
                    reason=result.reason,
                    model_name=model_name,
                    prompt_version="1",
                    created_at=utcnow(),
                )
            )
        else:
            job.status = "scored"
            session.add(
                Score(
                    job_id=job.id,
                    submission_id=submission.id,
                    suggested_score=result.score,
                    reason=result.reason,
                    model_name=model_name,
                    prompt_version="1",
                    created_at=utcnow(),
                )
            )
        job.claimed_at = job.lease_expires_at = None
        job.claim_token = None
        job.last_error_code = (
            job.last_error_code if job.status == "needs_a_person" else None
        )
        job.last_error_message = (
            job.last_error_message if job.status == "needs_a_person" else None
        )
        job.updated_at = utcnow()
        session.commit()


def run_once(
    session_factory: sessionmaker = SessionLocal,
    provider: ScoringProvider | None = None,
) -> bool:
    claim = claim_next_job(session_factory)
    if claim is None:
        return False
    process_claimed_job(session_factory, *claim, provider=provider)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the persistent scoring worker.")
    parser.add_argument("--poll-seconds", type=float, default=2)
    args = parser.parse_args()
    initialize_database()
    while True:
        if not run_once():
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
