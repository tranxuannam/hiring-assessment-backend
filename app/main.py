import hashlib
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.auth import (
    create_hr_session,
    resolve_candidate,
    resolve_hr_session,
    token_digest,
    utcnow,
)
from app.config import Settings, settings
from app.database import SessionLocal, get_session, initialize_database
from app.models import (
    AnswerSubmission,
    Candidate,
    HrReview,
    HrSession,
    InterviewResponse,
    Question,
    Role,
    Score,
    ScoringJob,
)
from app.schemas import (
    AnswerSubmissionReceipt,
    CandidateInterview,
    CandidateProgress,
    CandidateQuestion,
    AnswerSubmissionRequest,
    HrLoginRequest,
    HrReviewRequest,
)

API_PREFIX = "/api/v1"
MAX_IDEMPOTENCY_KEY = 128
SUPPORTED_AUDIO = {
    ".m4a": {"audio/mp4", "audio/x-m4a", "application/octet-stream"},
    ".mp3": {"audio/mpeg", "audio/mp3", "application/octet-stream"},
    ".wav": {"audio/wav", "audio/x-wav", "application/octet-stream"},
    ".webm": {"audio/webm", "application/octet-stream"},
    ".ogg": {"audio/ogg", "application/ogg", "application/octet-stream"},
}


def _progress(session: Session, candidate: Candidate) -> CandidateProgress:
    responses = session.scalars(
        select(InterviewResponse)
        .join(Question)
        .where(InterviewResponse.candidate_id == candidate.id)
        .order_by(Question.position)
    ).all()
    submitted = {
        response.question.external_id
        for response in responses
        if response.current_submission_id is not None
    }
    questions = session.scalars(select(Question).order_by(Question.position)).all()
    current = next((q.external_id for q in questions if q.external_id not in submitted), None)
    return CandidateProgress(
        current_question_external_id=current,
        submitted_question_ids=[
            q.external_id for q in questions if q.external_id in submitted
        ],
    )


def _ensure_candidate_responses(session: Session, candidate: Candidate) -> list[InterviewResponse]:
    questions = session.scalars(select(Question).order_by(Question.position)).all()
    for question in questions:
        response = session.scalar(
            select(InterviewResponse).where(
                InterviewResponse.candidate_id == candidate.id,
                InterviewResponse.question_id == question.id,
            )
        )
        if response is None:
            session.add(
                InterviewResponse(
                    candidate_id=candidate.id,
                    question_id=question.id,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
    session.flush()
    return session.scalars(
        select(InterviewResponse)
        .join(Question)
        .where(InterviewResponse.candidate_id == candidate.id)
        .order_by(Question.position)
    ).all()


def _receipt(session: Session, submission: AnswerSubmission) -> AnswerSubmissionReceipt:
    response = session.get(InterviewResponse, submission.response_id)
    question = session.get(Question, response.question_id)
    job = session.scalar(
        select(ScoringJob).where(ScoringJob.submission_id == submission.id)
    )
    if submission.source == "candidate_submission":
        initial_status = (
            "needs_a_person"
            if submission.audio_storage_key
            or not (submission.answer_text and submission.answer_text.strip())
            else "queued"
        )
        return AnswerSubmissionReceipt(
            submission_id=submission.id,
            question_id=question.external_id,
            received=True,
            scoring_status=initial_status,
            version=submission.version_number,
            current=True,
        )
    return AnswerSubmissionReceipt(
        submission_id=submission.id,
        question_id=question.external_id,
        received=True,
        scoring_status=job.status if job else "needs_a_person",
        version=submission.version_number,
        current=submission.is_current,
    )


def _require_idempotency_key(key: str | None) -> str:
    if key is None or not 8 <= len(key) <= MAX_IDEMPOTENCY_KEY:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "Idempotency-Key must contain 8 to 128 characters.",
            },
        )
    return key


def _payload_hash(value: dict[str, object]) -> str:
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _existing_idempotent_submission(
    session: Session, candidate: Candidate, key_hash: str, payload_hash: str
) -> AnswerSubmission | None:
    existing = session.scalar(
        select(AnswerSubmission).where(
            AnswerSubmission.candidate_id == candidate.id,
            AnswerSubmission.idempotency_key_hash == key_hash,
        )
    )
    if existing is None:
        return None
    if existing.payload_hash != payload_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_KEY_REUSED",
                "message": "This idempotency key was already used with a different payload.",
            },
        )
    return existing


def _current_version(session: Session, response_id: int) -> int:
    return (
        session.scalar(
            select(func.max(AnswerSubmission.version_number)).where(
                AnswerSubmission.response_id == response_id
            )
        )
        or 0
    )


def _new_scoring_job(session: Session, submission: AnswerSubmission, has_text: bool) -> None:
    now = utcnow()
    session.add(
        ScoringJob(
            submission_id=submission.id,
            status="queued" if has_text else "needs_a_person",
            attempt_count=0,
            max_attempts=settings.worker_max_attempts,
            next_attempt_at=now if has_text else None,
            last_error_code=None if has_text else "audio_or_empty_answer",
            last_error_message=None
            if has_text
            else "No transcript is available; manual review is required.",
            created_at=now,
            updated_at=now,
        )
    )


def _supersede_current(session: Session, response: InterviewResponse, now) -> None:
    previous_id = response.current_submission_id
    if previous_id is None:
        return
    previous = session.get(AnswerSubmission, previous_id)
    if previous is None:
        return
    previous.is_current = False
    previous.superseded_at = now
    old_job = session.scalar(
        select(ScoringJob).where(ScoringJob.submission_id == previous.id)
    )
    if old_job is not None and old_job.status == "queued" and old_job.claimed_at is None:
        old_job.status = "needs_a_person"
        old_job.last_error_code = "superseded_submission"
        old_job.last_error_message = "This version was replaced before scoring."
        old_job.next_attempt_at = None
        old_job.updated_at = now


def _is_valid_audio(extension: str, content_type: str, body: bytes) -> bool:
    if extension not in SUPPORTED_AUDIO:
        return False
    if content_type not in SUPPORTED_AUDIO[extension]:
        return False
    if extension == ".wav":
        return body.startswith(b"RIFF") and body[8:12] == b"WAVE"
    if extension == ".mp3":
        return body.startswith(b"ID3") or (
            len(body) >= 2 and body[0] == 0xFF and (body[1] & 0xE0) == 0xE0
        )
    if extension == ".ogg":
        return body.startswith(b"OggS")
    if extension == ".webm":
        return body.startswith(bytes.fromhex("1a45dfa3"))
    if extension == ".m4a":
        return len(body) >= 12 and body[4:8] == b"ftyp"
    return False


def create_app(
    session_factory: sessionmaker = SessionLocal,
    app_settings: Settings = settings,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        initialize_database(session_factory.kw["bind"])
        yield

    app = FastAPI(title="Hiring Assessment API", version="1.0.0", lifespan=lifespan)
    app.state.session_factory = session_factory
    app.state.settings = app_settings
    app.state.audio_storage_dir = app_settings.audio_storage_dir
    if session_factory is not SessionLocal:
        def override_session():
            with session_factory() as session:
                yield session

        app.dependency_overrides[get_session] = override_session

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and {"code", "message"} <= detail.keys():
            body = detail
        else:
            body = {"code": "HTTP_ERROR", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"code": "VALIDATION_ERROR", "message": "Request validation failed."},
        )

    @app.get(f"{API_PREFIX}/health")
    def health():
        return {"status": "ok"}

    @app.get(f"{API_PREFIX}/candidate/interview", response_model=CandidateInterview)
    def get_candidate_interview(
        candidate: Candidate = Depends(resolve_candidate),
        session: Session = Depends(get_session),
    ):
        responses = _ensure_candidate_responses(session, candidate)
        session.commit()
        return CandidateInterview(
            candidate={"display_name": candidate.name},
            questions=[
                CandidateQuestion(
                    id=response.id,
                    external_id=response.question.external_id,
                    position=response.question.position,
                    question_text=response.question.question_text,
                )
                for response in responses
            ],
            progress=_progress(session, candidate),
        )

    @app.get(f"{API_PREFIX}/candidate/progress", response_model=CandidateProgress)
    def get_candidate_progress(
        candidate: Candidate = Depends(resolve_candidate),
        session: Session = Depends(get_session),
    ):
        return _progress(session, candidate)

    @app.post(
        f"{API_PREFIX}/candidate/answers",
        response_model=AnswerSubmissionReceipt,
        status_code=201,
    )
    def submit_candidate_answer(
        payload: AnswerSubmissionRequest,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        candidate: Candidate = Depends(resolve_candidate),
        session: Session = Depends(get_session),
    ):
        key = _require_idempotency_key(idempotency_key)
        question = session.scalar(
            select(Question).where(Question.external_id == payload.question_id)
        )
        if question is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "VALIDATION_ERROR", "message": "Unknown question_id."},
            )
        response_slot = session.scalar(
            select(InterviewResponse).where(
                InterviewResponse.candidate_id == candidate.id,
                InterviewResponse.question_id == question.id,
            )
        )
        if response_slot is None:
            response_slot = InterviewResponse(
                candidate_id=candidate.id,
                question_id=question.id,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(response_slot)
            session.flush()
        now = utcnow()
        session.execute(
            update(InterviewResponse)
            .where(InterviewResponse.id == response_slot.id)
            .values(updated_at=now)
        )
        key_hash = token_digest(key)
        payload_hash = _payload_hash(
            {"question_id": payload.question_id, "answer_text": payload.answer_text}
        )
        existing = _existing_idempotent_submission(
            session, candidate, key_hash, payload_hash
        )
        if existing is not None:
            receipt = _receipt(session, existing)
            session.commit()
            response.status_code = 200
            return receipt

        _supersede_current(session, response_slot, now)
        submission = AnswerSubmission(
            candidate_id=candidate.id,
            response_id=response_slot.id,
            version_number=_current_version(session, response_slot.id) + 1,
            idempotency_key_hash=key_hash,
            payload_hash=payload_hash,
            answer_text=payload.answer_text,
            submitted_at=now,
            source="candidate_submission",
            is_current=True,
        )
        session.add(submission)
        session.flush()
        response_slot.current_submission_id = submission.id
        response_slot.updated_at = now
        _new_scoring_job(session, submission, bool(payload.answer_text.strip()))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = _existing_idempotent_submission(
                session, candidate, key_hash, payload_hash
            )
            if existing is None:
                raise
            response.status_code = 200
            return _receipt(session, existing)
        return _receipt(session, submission)

    @app.post(
        f"{API_PREFIX}/candidate/answers/{{response_id}}/audio",
        response_model=AnswerSubmissionReceipt,
        status_code=201,
    )
    async def upload_candidate_audio(
        response_id: int,
        response: Response,
        file: UploadFile = File(...),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        candidate: Candidate = Depends(resolve_candidate),
        session: Session = Depends(get_session),
    ):
        key = _require_idempotency_key(idempotency_key)
        response_slot = session.scalar(
            select(InterviewResponse).where(
                InterviewResponse.id == response_id,
                InterviewResponse.candidate_id == candidate.id,
            )
        )
        if response_slot is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": "Interview response not found."},
            )
        body = await file.read(app_settings.max_audio_bytes + 1)
        if len(body) > app_settings.max_audio_bytes:
            raise HTTPException(
                status_code=413,
                detail={"code": "PAYLOAD_TOO_LARGE", "message": "Audio file exceeds the configured size limit."},
            )
        extension = Path(file.filename or "").suffix.lower()
        content_type = (file.content_type or "").lower()
        if not _is_valid_audio(extension, content_type, body):
            raise HTTPException(
                status_code=415,
                detail={"code": "UNSUPPORTED_MEDIA_TYPE", "message": "Audio format or content type is not supported."},
            )
        payload_hash = _payload_hash(
            {
                "response_id": response_slot.id,
                "filename": Path(file.filename or "audio").name,
                "content_type": content_type,
                "content_sha256": hashlib.sha256(body).hexdigest(),
            }
        )
        now = utcnow()
        session.execute(
            update(InterviewResponse)
            .where(InterviewResponse.id == response_slot.id)
            .values(updated_at=now)
        )
        key_hash = token_digest(key)
        existing = _existing_idempotent_submission(
            session, candidate, key_hash, payload_hash
        )
        if existing is not None:
            receipt = _receipt(session, existing)
            session.commit()
            response.status_code = 200
            return receipt

        _supersede_current(session, response_slot, now)
        audio_key = f"candidate/{uuid.uuid4().hex}{extension}"
        audio_path = app.state.audio_storage_dir / audio_key
        submission = AnswerSubmission(
            candidate_id=candidate.id,
            response_id=response_slot.id,
            version_number=_current_version(session, response_slot.id) + 1,
            idempotency_key_hash=key_hash,
            payload_hash=payload_hash,
            audio_storage_key=audio_key,
            audio_original_filename=Path(file.filename or "audio").name[:255],
            audio_content_type=content_type,
            audio_size_bytes=len(body),
            submitted_at=now,
            source="candidate_submission",
            is_current=True,
        )
        try:
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(body)
            session.add(submission)
            session.flush()
            response_slot.current_submission_id = submission.id
            response_slot.updated_at = now
            _new_scoring_job(session, submission, False)
            session.commit()
        except IntegrityError:
            session.rollback()
            audio_path.unlink(missing_ok=True)
            existing = _existing_idempotent_submission(
                session, candidate, key_hash, payload_hash
            )
            if existing is None:
                raise
            response.status_code = 200
            return _receipt(session, existing)
        except Exception:
            session.rollback()
            audio_path.unlink(missing_ok=True)
            raise
        return _receipt(session, submission)

    @app.post(f"{API_PREFIX}/hr/auth/login")
    def hr_login(payload: HrLoginRequest, response: Response, session: Session = Depends(get_session)):
        valid_user = secrets.compare_digest(
            payload.username.encode(), app_settings.hr_demo_username.encode()
        )
        valid_password = secrets.compare_digest(
            payload.password.encode(), app_settings.hr_demo_password.encode()
        )
        if not (valid_user and valid_password):
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "Invalid mocked HR credentials."},
            )
        raw_token = create_hr_session(session, payload.username)
        session.commit()
        response.set_cookie(
            "hr_session",
            raw_token,
            httponly=True,
            secure=app_settings.cookie_secure,
            samesite="lax",
            max_age=app_settings.hr_session_hours * 3600,
            path=f"{API_PREFIX}/hr",
        )
        return {"authenticated": True, "mode": "mocked"}

    @app.get(f"{API_PREFIX}/hr/auth/session")
    def hr_session_status(hr: HrSession = Depends(resolve_hr_session)):
        return {"authenticated": True, "mode": "mocked"}

    @app.get(f"{API_PREFIX}/hr/roles")
    def list_roles(
        _hr: HrSession = Depends(resolve_hr_session),
        session: Session = Depends(get_session),
    ):
        return {
            "data": [
                {"id": role.id, "name": role.name}
                for role in session.scalars(select(Role).order_by(Role.name))
            ]
        }

    @app.get(f"{API_PREFIX}/hr/roles/{{role_id}}/candidates")
    def list_role_candidates(
        role_id: int,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        _hr: HrSession = Depends(resolve_hr_session),
        session: Session = Depends(get_session),
    ):
        if session.get(Role, role_id) is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Role not found."})
        candidates_query = select(Candidate).where(Candidate.role_id == role_id)
        total = session.scalar(
            select(func.count()).select_from(Candidate).where(Candidate.role_id == role_id)
        ) or 0
        candidates = session.scalars(
            candidates_query.order_by(Candidate.name, Candidate.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        data = []
        for candidate in candidates:
            current_submissions = select(AnswerSubmission.id).join(
                InterviewResponse,
                AnswerSubmission.response_id == InterviewResponse.id,
            ).where(
                InterviewResponse.candidate_id == candidate.id,
                InterviewResponse.current_submission_id == AnswerSubmission.id,
            )
            answer_count = session.scalar(
                select(func.count()).select_from(current_submissions.subquery())
            ) or 0
            scored_count = session.scalar(
                select(func.count())
                .select_from(ScoringJob)
                .join(AnswerSubmission)
                .where(
                    AnswerSubmission.id.in_(current_submissions),
                    ScoringJob.status == "scored",
                )
            ) or 0
            failed_count = session.scalar(
                select(func.count())
                .select_from(ScoringJob)
                .join(AnswerSubmission)
                .where(
                    AnswerSubmission.id.in_(current_submissions),
                    ScoringJob.status == "failed",
                )
            ) or 0
            answer_rows = session.execute(
                select(AnswerSubmission, Question, ScoringJob)
                .join(
                    InterviewResponse,
                    AnswerSubmission.response_id == InterviewResponse.id,
                )
                .join(Question, InterviewResponse.question_id == Question.id)
                .outerjoin(ScoringJob, ScoringJob.submission_id == AnswerSubmission.id)
                .where(InterviewResponse.candidate_id == candidate.id)
                .order_by(Question.position, AnswerSubmission.version_number)
            ).all()
            data.append(
                {
                    "candidate_id": candidate.id,
                    "external_id": candidate.external_id,
                    "name": candidate.name,
                    "answer_count": answer_count,
                    "scored_count": scored_count,
                    "failed_count": failed_count,
                    "submissions": [
                        {
                            "submission_id": submission.id,
                            "question_id": question.external_id,
                            "version": submission.version_number,
                            "is_current": submission.is_current,
                            "scoring_status": job.status if job else None,
                            "submitted_at": submission.submitted_at.isoformat(),
                        }
                        for submission, question, job in answer_rows
                    ],
                }
            )
        return {
            "data": data,
            "pagination": {"page": page, "page_size": page_size, "total": total},
        }

    @app.get(f"{API_PREFIX}/hr/answers/{{submission_id}}")
    def get_hr_answer_detail(
        submission_id: int,
        request: Request,
        _hr: HrSession = Depends(resolve_hr_session),
        session: Session = Depends(get_session),
    ):
        submission = session.get(AnswerSubmission, submission_id)
        if submission is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Answer not found."})
        response_slot = session.get(InterviewResponse, submission.response_id)
        question = session.get(Question, response_slot.question_id)
        candidate = session.get(Candidate, submission.candidate_id)
        role = session.get(Role, candidate.role_id)
        job = session.scalar(
            select(ScoringJob).where(ScoringJob.submission_id == submission.id)
        )
        score = session.scalar(select(Score).where(Score.submission_id == submission.id))
        latest_review = session.scalar(
            select(HrReview)
            .where(HrReview.submission_id == submission.id)
            .order_by(HrReview.created_at.desc(), HrReview.id.desc())
        )
        audio_url = (
            str(request.url_for("download_hr_audio", submission_id=submission.id))
            if submission.audio_storage_key
            else None
        )
        return {
            "submission_id": submission.id,
            "external_answer_id": submission.external_id,
            "candidate": {
                "external_id": candidate.external_id,
                "name": candidate.name,
                "role": role.name,
            },
            "question": {
                "external_id": question.external_id,
                "question_text": question.question_text,
                "grading_guide": question.grading_guide,
            },
            "answer_text": submission.answer_text,
            "audio_available": submission.audio_storage_key is not None,
            "audio_download_url": audio_url,
            "submitted_at": submission.submitted_at.isoformat(),
            "version": submission.version_number,
            "is_current": submission.is_current,
            "scoring_job": _job_dto(job) if job else None,
            "suggested_score": score.suggested_score if score else None,
            "reason": score.reason if score else None,
            "hr_final_score": latest_review.final_score if latest_review else None,
            "hr_override_reason": latest_review.reason if latest_review else None,
        }

    @app.get(f"{API_PREFIX}/hr/answers/{{submission_id}}/audio", name="download_hr_audio")
    def download_hr_audio(
        submission_id: int,
        _hr: HrSession = Depends(resolve_hr_session),
        session: Session = Depends(get_session),
    ):
        submission = session.get(AnswerSubmission, submission_id)
        if submission is None or not submission.audio_storage_key:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Audio not found."})
        storage_root = app.state.audio_storage_dir.resolve()
        audio_path = (storage_root / submission.audio_storage_key).resolve()
        if storage_root not in audio_path.parents or not audio_path.is_file():
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Audio not found."})
        return FileResponse(
            audio_path,
            media_type=submission.audio_content_type or "application/octet-stream",
            filename=Path(submission.audio_original_filename or "answer-audio").name,
        )

    @app.get(f"{API_PREFIX}/hr/scoring-jobs")
    def list_scoring_jobs(
        status: str | None = Query(default=None, pattern="^(queued|scored|failed|needs_a_person)$"),
        page: int = Query(default=1, ge=1),
        _hr: HrSession = Depends(resolve_hr_session),
        session: Session = Depends(get_session),
    ):
        query = select(ScoringJob)
        if status:
            query = query.where(ScoringJob.status == status)
        jobs = session.scalars(
            query.order_by(ScoringJob.created_at.desc(), ScoringJob.id.desc())
            .offset((page - 1) * 50)
            .limit(50)
        ).all()
        return {"data": [_job_dto(job) for job in jobs]}

    @app.post(f"{API_PREFIX}/hr/scoring-jobs/{{job_id}}/retry", status_code=202)
    def retry_scoring_job(
        job_id: int,
        _hr: HrSession = Depends(resolve_hr_session),
        session: Session = Depends(get_session),
    ):
        job = session.get(ScoringJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Scoring job not found."})
        if job.status != "failed":
            raise HTTPException(status_code=409, detail={"code": "CONFLICT", "message": "Only failed scoring jobs can be retried."})
        job.status = "queued"
        job.attempt_count = 0
        job.max_attempts = app_settings.worker_max_attempts
        job.next_attempt_at = utcnow()
        job.claimed_at = job.lease_expires_at = None
        job.claim_token = None
        job.last_error_code = job.last_error_message = None
        job.updated_at = utcnow()
        session.commit()
        return _job_dto(job)

    @app.put(f"{API_PREFIX}/hr/answers/{{submission_id}}/review")
    def override_suggested_score(
        submission_id: int,
        payload: HrReviewRequest,
        hr: HrSession = Depends(resolve_hr_session),
        session: Session = Depends(get_session),
    ):
        submission = session.get(AnswerSubmission, submission_id)
        if submission is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Answer not found."})
        score = session.scalar(select(Score).where(Score.submission_id == submission.id))
        review = HrReview(
            submission_id=submission.id,
            reviewer_id=hr.reviewer_id,
            final_score=payload.final_score,
            reason=payload.reason,
            created_at=utcnow(),
        )
        session.add(review)
        session.commit()
        return {
            "submission_id": submission.id,
            "suggested_score": score.suggested_score if score else None,
            "final_score": review.final_score,
            "reason": review.reason,
        }

    return app


def _job_dto(job: ScoringJob) -> dict[str, object]:
    return {
        "id": job.id,
        "status": job.status,
        "attempt_count": job.attempt_count,
        "can_retry": job.status == "failed",
        "error_message": (
            job.last_error_message
            if job.status in {"failed", "needs_a_person"}
            else None
        ),
    }


app = create_app()
