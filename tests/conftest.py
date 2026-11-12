from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_candidate_token
from app.config import settings
from app.database import build_engine
from app.importer import import_dataset
from app.main import create_app
from app.models import Candidate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def backend(tmp_path):
    database_path = tmp_path / "test.sqlite"
    engine = build_engine(f"sqlite:///{database_path}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    audio_dir = tmp_path / "audio"
    app_settings = replace(
        settings,
        database_url=f"sqlite:///{database_path}",
        audio_storage_dir=audio_dir,
        hr_demo_username="hr-test",
        hr_demo_password="password-test",
        cookie_secure=False,
        max_audio_bytes=1024 * 1024,
    )
    import_dataset(ROOT / "data" / "source", session_factory, audio_dir)
    with session_factory() as session:
        candidate = session.query(Candidate).filter_by(external_id="C2041").one()
        token = create_candidate_token(session, candidate)
        session.commit()
    app = create_app(session_factory, app_settings)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            engine=engine,
            session_factory=session_factory,
            candidate_token=token,
            audio_dir=audio_dir,
            app_settings=app_settings,
        )
    engine.dispose()
