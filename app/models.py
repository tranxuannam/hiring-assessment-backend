from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    candidates: Mapped[list["Candidate"]] = relationship(back_populates="role")


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    applied_on: Mapped[date | None] = mapped_column(Date)
    cv_tier: Mapped[str | None] = mapped_column(String(32))
    invited_on: Mapped[date | None] = mapped_column(Date)
    answers_submitted_on: Mapped[date | None] = mapped_column(Date)
    pipeline_outcome: Mapped[str | None] = mapped_column(String(64))
    outcome_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    role: Mapped[Role] = relationship(back_populates="candidates")
    tokens: Mapped[list["CandidateAccessToken"]] = relationship(back_populates="candidate")
    responses: Mapped[list["InterviewResponse"]] = relationship(back_populates="candidate")


class CandidateAccessToken(Base):
    __tablename__ = "candidate_access_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    candidate: Mapped[Candidate] = relationship(back_populates="tokens")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    grading_guide: Mapped[str] = mapped_column(Text, nullable=False)


class InterviewResponse(Base):
    __tablename__ = "interview_responses"
    __table_args__ = (UniqueConstraint("candidate_id", "question_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), nullable=False)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), nullable=False)
    current_submission_id: Mapped[int | None] = mapped_column(
        ForeignKey("answer_submissions.id", use_alter=True, name="fk_response_current_submission")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    candidate: Mapped[Candidate] = relationship(back_populates="responses")
    question: Mapped[Question] = relationship()
    submissions: Mapped[list["AnswerSubmission"]] = relationship(
        back_populates="response", foreign_keys="AnswerSubmission.response_id"
    )
    current_submission: Mapped["AnswerSubmission | None"] = relationship(
        foreign_keys=[current_submission_id], post_update=True
    )


class AnswerSubmission(Base):
    __tablename__ = "answer_submissions"
    __table_args__ = (
        UniqueConstraint("response_id", "version_number"),
        UniqueConstraint("candidate_id", "idempotency_key_hash"),
        Index("ix_submissions_candidate_key", "candidate_id", "idempotency_key_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), nullable=False)
    response_id: Mapped[int] = mapped_column(ForeignKey("interview_responses.id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64))
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    answer_text: Mapped[str | None] = mapped_column(Text)
    audio_storage_key: Mapped[str | None] = mapped_column(String(500))
    audio_original_filename: Mapped[str | None] = mapped_column(String(255))
    audio_content_type: Mapped[str | None] = mapped_column(String(128))
    audio_size_bytes: Mapped[int | None] = mapped_column(Integer)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime)
    response: Mapped[InterviewResponse] = relationship(
        back_populates="submissions", foreign_keys=[response_id]
    )
    scoring_job: Mapped["ScoringJob | None"] = relationship(
        back_populates="submission", uselist=False
    )
    scores: Mapped[list["Score"]] = relationship(back_populates="submission")
    reviews: Mapped[list["HrReview"]] = relationship(back_populates="submission")


class ScoringJob(Base):
    __tablename__ = "scoring_jobs"
    __table_args__ = (Index("ix_scoring_jobs_claim", "status", "next_attempt_at", "lease_expires_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("answer_submissions.id"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    submission: Mapped[AnswerSubmission] = relationship(back_populates="scoring_job")
    score: Mapped["Score | None"] = relationship(back_populates="job", uselist=False)


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("scoring_jobs.id"), unique=True, nullable=False)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("answer_submissions.id"), unique=True, nullable=False
    )
    suggested_score: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    job: Mapped[ScoringJob] = relationship(back_populates="score")
    submission: Mapped[AnswerSubmission] = relationship(back_populates="scores")


class HrReview(Base):
    __tablename__ = "hr_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("answer_submissions.id"), nullable=False, index=True
    )
    reviewer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    final_score: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    submission: Mapped[AnswerSubmission] = relationship(back_populates="reviews")


class HrSession(Base):
    __tablename__ = "hr_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
