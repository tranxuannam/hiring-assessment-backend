import argparse
import csv
import json
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.auth import create_candidate_token, utcnow
from app.config import settings
from app.database import SessionLocal, initialize_database
from app.models import (
    AnswerSubmission,
    Candidate,
    CandidateAccessToken,
    InterviewResponse,
    Question,
    Role,
    ScoringJob,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "data" / "source"


def parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_questions(path: Path) -> list[dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^## (Q\d+)\.\s*(.*?)\s*$", text)
    questions = []
    for index in range(1, len(sections), 3):
        external_id, title, body = sections[index : index + 3]
        lines = [line.strip() for line in body.strip().splitlines() if line.strip()]
        guide_lines = [
            re.sub(r"^- \*\*(\d):\*\*\s*", r"\1: ", line)
            for line in lines
            if line.startswith("- **")
        ]
        questions.append(
            {
                "external_id": external_id,
                "position": int(external_id[1:]),
                "question_text": title.strip(),
                "grading_guide": "\n".join(guide_lines),
            }
        )
    if len(questions) != 6:
        raise ValueError(f"Expected 6 question definitions in {path}, found {len(questions)}")
    return questions


def _ensure_candidate_response(
    session, candidate: Candidate, question: Question
) -> InterviewResponse:
    response = session.scalar(
        select(InterviewResponse).where(
            InterviewResponse.candidate_id == candidate.id,
            InterviewResponse.question_id == question.id,
        )
    )
    if response is None:
        response = InterviewResponse(
            candidate_id=candidate.id,
            question_id=question.id,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(response)
        session.flush()
    return response


def import_dataset(
    source_dir: Path = SOURCE_DIR,
    session_factory: sessionmaker = SessionLocal,
    storage_dir: Path | None = None,
) -> dict[str, object]:
    storage_dir = storage_dir or settings.audio_storage_dir
    bind = session_factory.kw["bind"]
    initialize_database(bind)
    newly_issued_tokens: dict[str, str] = {}
    with session_factory() as session:
        with (source_dir / "pipeline_log.csv").open(
            encoding="utf-8-sig", newline=""
        ) as pipeline_file:
            pipeline_rows = list(csv.DictReader(pipeline_file))

        roles: dict[str, Role] = {}
        for row in pipeline_rows:
            role = session.scalar(select(Role).where(Role.name == row["role"]))
            if role is None:
                role = Role(name=row["role"])
                session.add(role)
                session.flush()
            roles[role.name] = role

        candidates: dict[str, Candidate] = {}
        for row in pipeline_rows:
            candidate = session.scalar(
                select(Candidate).where(Candidate.external_id == row["candidate_id"])
            )
            if candidate is None:
                candidate = Candidate(
                    external_id=row["candidate_id"],
                    role_id=roles[row["role"]].id,
                    name=row["candidate_name"],
                    email=row["candidate_email"],
                    applied_on=parse_date(row["applied_on"]),
                    cv_tier=row["cv_tier"] or None,
                    invited_on=parse_date(row["invited_on"]),
                    answers_submitted_on=parse_date(row["answers_submitted_on"]),
                    pipeline_outcome=row["outcome"] or None,
                    outcome_on=parse_date(row["outcome_on"]),
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                session.add(candidate)
                session.flush()
            else:
                candidate.role_id = roles[row["role"]].id
                candidate.name = row["candidate_name"]
                candidate.email = row["candidate_email"]
                candidate.applied_on = parse_date(row["applied_on"])
                candidate.cv_tier = row["cv_tier"] or None
                candidate.invited_on = parse_date(row["invited_on"])
                candidate.answers_submitted_on = parse_date(row["answers_submitted_on"])
                candidate.pipeline_outcome = row["outcome"] or None
                candidate.outcome_on = parse_date(row["outcome_on"])
                candidate.updated_at = utcnow()
            candidates[candidate.external_id] = candidate
            active_token = session.scalar(
                select(CandidateAccessToken.id).where(
                    CandidateAccessToken.candidate_id == candidate.id,
                    CandidateAccessToken.revoked_at.is_(None),
                    CandidateAccessToken.expires_at > utcnow(),
                )
            )
            if active_token is None:
                newly_issued_tokens[candidate.external_id] = create_candidate_token(
                    session, candidate
                )

        question_map: dict[str, Question] = {}
        for question_data in load_questions(source_dir / "questions.md"):
            question = session.scalar(
                select(Question).where(
                    Question.external_id == question_data["external_id"]
                )
            )
            if question is None:
                question = Question(**question_data)
                session.add(question)
                session.flush()
            else:
                question.position = question_data["position"]
                question.question_text = question_data["question_text"]
                question.grading_guide = question_data["grading_guide"]
            question_map[question.external_id] = question

        answer_paths = sorted((source_dir / "answers").glob("*.json"))
        parsed_answers = [
            json.loads(path.read_text(encoding="utf-8")) for path in answer_paths
        ]
        parsed_answers.sort(key=lambda answer: (answer["submitted_at"], answer["answer_id"]))
        for answer in parsed_answers:
            if session.scalar(
                select(AnswerSubmission.id).where(
                    AnswerSubmission.external_id == answer["answer_id"]
                )
            ):
                continue
            candidate = candidates[answer["candidate_id"]]
            question = question_map[answer["question_id"]]
            response = _ensure_candidate_response(session, candidate, question)
            audio_name = answer.get("audio_file")
            audio_key = None
            audio_type = None
            audio_size = None
            if audio_name:
                source_audio = source_dir / "answers" / audio_name
                if not source_audio.is_file():
                    raise FileNotFoundError(f"Missing source audio for {answer['answer_id']}")
                audio_key = f"imports/{answer['answer_id']}{source_audio.suffix.lower()}"
                target_audio = storage_dir / audio_key
                target_audio.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_audio, target_audio)
                audio_type = "audio/mp4" if source_audio.suffix.lower() == ".m4a" else "application/octet-stream"
                audio_size = source_audio.stat().st_size
            latest = session.scalar(
                select(AnswerSubmission)
                .where(AnswerSubmission.response_id == response.id)
                .order_by(AnswerSubmission.version_number.desc())
            )
            submission = AnswerSubmission(
                external_id=answer["answer_id"],
                candidate_id=candidate.id,
                response_id=response.id,
                version_number=(latest.version_number + 1) if latest else 1,
                answer_text=answer.get("transcript"),
                audio_storage_key=audio_key,
                audio_original_filename=audio_name,
                audio_content_type=audio_type,
                audio_size_bytes=audio_size,
                submitted_at=parse_datetime(answer["submitted_at"]),
                duration_seconds=answer.get("duration_seconds"),
                source="data_pack",
                is_current=False,
            )
            session.add(submission)
            session.flush()

        responses = session.scalars(select(InterviewResponse)).all()
        for response in responses:
            imported_submissions = session.scalars(
                select(AnswerSubmission)
                .where(
                    AnswerSubmission.response_id == response.id,
                    AnswerSubmission.source == "data_pack",
                )
                .order_by(AnswerSubmission.submitted_at, AnswerSubmission.external_id)
            ).all()
            current = response.current_submission
            selected = None
            if imported_submissions and (
                current is None or current.source == "data_pack"
            ):
                selected = imported_submissions[-1]
                for submission in imported_submissions:
                    submission.is_current = submission.id == selected.id
                    submission.superseded_at = (
                        None if submission.id == selected.id else selected.submitted_at
                    )
                response.current_submission_id = selected.id
                response.updated_at = selected.submitted_at
            elif current is not None:
                for submission in imported_submissions:
                    submission.is_current = False

            for submission in imported_submissions:
                is_active = selected is not None and submission.id == selected.id
                job = session.scalar(
                    select(ScoringJob).where(ScoringJob.submission_id == submission.id)
                )
                has_text = bool(submission.answer_text and submission.answer_text.strip())
                if job is None:
                    waiting_for_review = not is_active or not has_text
                    job = ScoringJob(
                        submission_id=submission.id,
                        status="queued" if is_active and has_text else "needs_a_person",
                        attempt_count=0,
                        max_attempts=settings.worker_max_attempts,
                        next_attempt_at=utcnow() if is_active and has_text else None,
                        last_error_code=(
                            "audio_or_empty_answer"
                            if is_active and waiting_for_review
                            else "superseded_submission"
                            if not is_active
                            else None
                        ),
                        last_error_message=None
                        if is_active and has_text
                        else "This version was replaced; only the current answer is scored."
                        if not is_active
                        else "No transcript is available; manual review is required.",
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    session.add(job)
                elif not is_active and job.status == "queued" and job.claimed_at is None:
                    job.status = "needs_a_person"
                    job.next_attempt_at = None
                    job.last_error_code = "superseded_submission"
                    job.last_error_message = "This version was replaced; only the current answer is scored."
                    job.updated_at = utcnow()
                elif (
                    is_active
                    and job.status == "needs_a_person"
                    and job.last_error_code == "superseded_submission"
                ):
                    job.status = "queued" if has_text else "needs_a_person"
                    job.next_attempt_at = utcnow() if has_text else None
                    job.last_error_code = None if has_text else "audio_or_empty_answer"
                    job.last_error_message = (
                        None
                        if has_text
                        else "No transcript is available; manual review is required."
                    )
                    job.updated_at = utcnow()
        session.commit()

        return {
            "pipeline_rows": len(pipeline_rows),
            "candidates": len(candidates),
            "roles": len(roles),
            "questions": len(question_map),
            "answer_records": len(parsed_answers),
            "audio_files": sum(bool(item.get("audio_file")) for item in parsed_answers),
            "new_tokens": newly_issued_tokens,
        }


def issue_candidate_token(
    external_id: str, session_factory: sessionmaker = SessionLocal
) -> str:
    initialize_database(session_factory.kw["bind"])
    with session_factory() as session:
        candidate = session.scalar(
            select(Candidate).where(Candidate.external_id == external_id)
        )
        if candidate is None:
            raise ValueError(f"Unknown candidate: {external_id}")
        now = utcnow()
        session.query(CandidateAccessToken).filter(
            CandidateAccessToken.candidate_id == candidate.id,
            CandidateAccessToken.revoked_at.is_(None),
        ).update({"revoked_at": now}, synchronize_session=False)
        raw_token = create_candidate_token(session, candidate)
        session.commit()
        return raw_token


def create_candidate_with_token(
    external_id: str,
    name: str,
    email: str,
    role_name: str,
    session_factory: sessionmaker = SessionLocal,
) -> str:
    external_id = external_id.strip()
    name = name.strip()
    email = email.strip()
    role_name = role_name.strip()
    if not external_id or not name or not email or not role_name:
        raise ValueError("Candidate ID, name, email, and role are required")

    initialize_database(session_factory.kw["bind"])
    with session_factory() as session:
        existing_candidate_id = session.scalar(
            select(Candidate.id).where(Candidate.external_id == external_id)
        )
        if existing_candidate_id is not None:
            raise ValueError(f"Candidate already exists: {external_id}")
        role = session.scalar(select(Role).where(Role.name == role_name))
        if role is None:
            raise ValueError(f"Unknown role: {role_name}")

        now = utcnow()
        candidate = Candidate(
            external_id=external_id,
            role_id=role.id,
            name=name,
            email=email,
            created_at=now,
            updated_at=now,
        )
        session.add(candidate)
        session.flush()
        raw_token = create_candidate_token(session, candidate)
        session.commit()
        return raw_token


def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotently import the hiring data pack.")
    parser.add_argument("--source-dir", type=Path, default=SOURCE_DIR)
    token_action = parser.add_mutually_exclusive_group()
    token_action.add_argument(
        "--issue-token",
        metavar="CANDIDATE_ID",
        help="rotate one candidate's token and display it once after import",
    )
    token_action.add_argument(
        "--create-candidate",
        metavar="CANDIDATE_ID",
        help="create a candidate in an existing role and display a token once",
    )
    parser.add_argument("--name", help="new candidate's name")
    parser.add_argument("--email", help="new candidate's email")
    parser.add_argument("--role", help="existing role name for the new candidate")
    args = parser.parse_args()

    if args.create_candidate:
        if not args.name or not args.email or not args.role:
            parser.error("--create-candidate requires --name, --email, and --role")
        try:
            raw_token = create_candidate_with_token(
                args.create_candidate,
                args.name,
                args.email,
                args.role,
            )
        except ValueError as error:
            parser.error(str(error))
        print(f"One-time candidate token for {args.create_candidate}: {raw_token}")
        return

    result = import_dataset(args.source_dir)
    result.pop("new_tokens")
    print(json.dumps(result, indent=2))
    if args.issue_token:
        print(
            f"One-time candidate token for {args.issue_token}: "
            f"{issue_candidate_token(args.issue_token)}"
        )


if __name__ == "__main__":
    main()
