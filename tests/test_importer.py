import pytest
from sqlalchemy import func, select

from app.auth import token_digest
from app.database import build_engine
from app.importer import SOURCE_DIR, create_candidate_with_token, import_dataset
from app.models import (
    AnswerSubmission,
    Candidate,
    CandidateAccessToken,
    InterviewResponse,
    Question,
    Role,
    ScoringJob,
)


def test_import_is_repeatable_and_preserves_latest_duplicate(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'import.sqlite'}")
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    storage_dir = tmp_path / "audio"

    first = import_dataset(SOURCE_DIR, factory, storage_dir)
    second = import_dataset(SOURCE_DIR, factory, storage_dir)
    with factory() as session:
        assert first["pipeline_rows"] == 496
        assert first["answer_records"] == 35
        assert first["audio_files"] == 5
        assert second["candidates"] == 496
        assert session.scalar(select(func.count()).select_from(Candidate)) == 496
        assert session.scalar(select(func.count()).select_from(Role)) == 5
        assert session.scalar(select(func.count()).select_from(Question)) == 6
        assert session.scalar(select(func.count()).select_from(AnswerSubmission)) == 35
        assert session.scalar(select(func.count()).select_from(ScoringJob)) == 35
        assert session.scalar(select(func.count()).select_from(CandidateAccessToken)) == 496
        audio_only = session.scalar(
            select(AnswerSubmission)
            .where(AnswerSubmission.external_id == "A031")
        )
        audio_job = session.scalar(
            select(ScoringJob).where(ScoringJob.submission_id == audio_only.id)
        )
        assert audio_job.status == "needs_a_person"
        assert audio_only.answer_text is None
        response = session.scalar(
            select(InterviewResponse)
            .join(Candidate)
            .join(Question)
            .where(
                Candidate.external_id == "C2052",
                Question.external_id == "Q3",
            )
        )
        versions = session.scalars(
            select(AnswerSubmission)
            .where(AnswerSubmission.response_id == response.id)
            .order_by(AnswerSubmission.version_number)
        ).all()
        assert [version.external_id for version in versions] == ["A022", "A023"]
        assert versions[0].is_current is False
        assert versions[1].is_current is True
        assert response.current_submission_id == versions[1].id
        old_job = session.scalar(
            select(ScoringJob).where(ScoringJob.submission_id == versions[0].id)
        )
        assert old_job.status == "needs_a_person"
        assert old_job.last_error_code == "superseded_submission"
        assert all(
            (storage_dir / f"imports/A{i:03}.m4a").is_file()
            for i in range(31, 36)
        )
    engine.dispose()


def test_candidate_tokens_are_hashed(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'token.sqlite'}")
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    result = import_dataset(SOURCE_DIR, factory, tmp_path / "audio")
    with factory() as session:
        stored_hashes = set(session.scalars(select(CandidateAccessToken.token_hash)))
        assert result["new_tokens"]["C2041"] not in stored_hashes
        assert len(stored_hashes) == 496
    engine.dispose()


def test_create_candidate_stores_only_hashed_token(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'new-candidate.sqlite'}")
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    import_dataset(SOURCE_DIR, factory, tmp_path / "audio")

    raw_token = create_candidate_with_token(
        "497",
        "Nam Tran",
        "hienmong2002@gmail.com",
        "Backend Engineer",
        factory,
    )
    with factory() as session:
        candidate = session.scalar(
            select(Candidate).where(Candidate.external_id == "497")
        )
        token = session.scalar(
            select(CandidateAccessToken).where(
                CandidateAccessToken.candidate_id == candidate.id
            )
        )
        assert candidate.name == "Nam Tran"
        assert candidate.email == "hienmong2002@gmail.com"
        assert candidate.role.name == "Backend Engineer"
        assert token.token_hash == token_digest(raw_token)
        assert token.token_hash != raw_token

    engine.dispose()


def test_create_candidate_rejects_duplicate_id_and_unknown_role(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'candidate-validation.sqlite'}")
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    import_dataset(SOURCE_DIR, factory, tmp_path / "audio")

    with pytest.raises(ValueError, match="already exists"):
        create_candidate_with_token(
            "C2041", "Other Name", "other@example.com", "Backend Engineer", factory
        )
    with pytest.raises(ValueError, match="Unknown role"):
        create_candidate_with_token(
            "497", "Nam Tran", "hienmong2002@gmail.com", "Missing Role", factory
        )
    with factory() as session:
        assert session.scalar(
            select(Candidate).where(Candidate.external_id == "497")
        ) is None

    engine.dispose()
