import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.models import Candidate, CandidateAccessToken, HrSession


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_candidate_token(session: Session, candidate: Candidate) -> str:
    raw_token = secrets.token_urlsafe(32)
    session.add(
        CandidateAccessToken(
            candidate_id=candidate.id,
            token_hash=token_digest(raw_token),
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(days=settings.candidate_token_days),
        )
    )
    return raw_token


def resolve_candidate(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> Candidate:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Valid candidate token required"})
    raw_token = authorization[7:].strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Valid candidate token required"})
    token = session.scalar(
        select(CandidateAccessToken).where(
            CandidateAccessToken.token_hash == token_digest(raw_token),
            CandidateAccessToken.revoked_at.is_(None),
            CandidateAccessToken.expires_at > utcnow(),
        )
    )
    if token is None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "Valid candidate token required"})
    return session.get(Candidate, token.candidate_id)


def create_hr_session(session: Session, reviewer_id: str) -> str:
    raw_token = secrets.token_urlsafe(32)
    session.add(
        HrSession(
            token_hash=token_digest(raw_token),
            reviewer_id=reviewer_id,
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(hours=settings.hr_session_hours),
        )
    )
    return raw_token


def resolve_hr_session(
    hr_session: str | None = Cookie(default=None, alias="hr_session"),
    session: Session = Depends(get_session),
) -> HrSession:
    if not hr_session:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "HR session required"})
    record = session.scalar(
        select(HrSession).where(
            HrSession.token_hash == token_digest(hr_session),
            HrSession.revoked_at.is_(None),
            HrSession.expires_at > utcnow(),
        )
    )
    if record is None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "HR session required"})
    return record
