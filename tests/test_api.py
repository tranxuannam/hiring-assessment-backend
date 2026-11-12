from sqlalchemy import select

from app.models import (
    AnswerSubmission,
    Candidate,
    HrReview,
    InterviewResponse,
    Question,
    ScoringJob,
)
from app.auth import create_candidate_token


def _candidate_headers(backend):
    return {"Authorization": f"Bearer {backend.candidate_token}"}


def _login_hr(backend):
    response = backend.client.post(
        "/api/v1/hr/auth/login",
        json={"username": "hr-test", "password": "password-test"},
    )
    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "mode": "mocked"}
    return response


def test_candidate_interview_is_private_and_contains_no_hr_data(backend):
    response = backend.client.get(
        "/api/v1/candidate/interview", headers=_candidate_headers(backend)
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["questions"]) == 6
    assert payload["progress"]["current_question_external_id"] == "Q3"
    assert "grading_guide" not in response.text
    assert "suggested_score" not in response.text
    assert "hr_reference" not in response.text
    assert backend.client.get("/api/v1/candidate/interview").status_code == 401
    with backend.session_factory() as session:
        other_candidate = session.query(Candidate).filter_by(external_id="C2056").one()
        other_token = create_candidate_token(session, other_candidate)
        session.commit()
    foreign_upload = backend.client.post(
        f"/api/v1/candidate/answers/{payload['questions'][0]['id']}/audio",
        headers={
            "Authorization": f"Bearer {other_token}",
            "Idempotency-Key": "foreign-audio-key",
        },
        files={"file": ("response.m4a", b"\x00\x00\x00\x18ftypisomfake", "audio/mp4")},
    )
    assert foreign_upload.status_code == 404


def test_submission_idempotency_versions_and_conflicts(backend):
    headers = {**_candidate_headers(backend), "Idempotency-Key": "candidate-key-0001"}
    body = {"question_id": "Q3", "answer_text": "I prioritized work by impact."}
    first = backend.client.post("/api/v1/candidate/answers", headers=headers, json=body)
    assert first.status_code == 201
    replay = backend.client.post("/api/v1/candidate/answers", headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    changed = backend.client.post(
        "/api/v1/candidate/answers",
        headers=headers,
        json={"question_id": "Q3", "answer_text": "Different payload."},
    )
    assert changed.status_code == 409
    assert changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"

    second_headers = {
        **_candidate_headers(backend),
        "Idempotency-Key": "candidate-key-0002",
    }
    second = backend.client.post(
        "/api/v1/candidate/answers", headers=second_headers, json=body
    )
    assert second.status_code == 201
    assert second.json()["version"] == 2
    assert second.json()["current"] is True
    replay_after_newer = backend.client.post(
        "/api/v1/candidate/answers", headers=headers, json=body
    )
    assert replay_after_newer.json() == first.json()
    with backend.session_factory() as session:
        submissions = session.scalars(
            select(AnswerSubmission)
            .join(InterviewResponse, AnswerSubmission.response_id == InterviewResponse.id)
            .join(Question, InterviewResponse.question_id == Question.id)
            .where(Question.external_id == "Q3")
        ).all()
        candidate_submissions = [
            item
            for item in submissions
            if item.candidate_id
            == session.scalar(
                select(Candidate.id).where(Candidate.external_id == "C2041")
            )
        ]
        assert len(candidate_submissions) == 2
        assert sum(item.is_current for item in candidate_submissions) == 1
        jobs = session.scalars(
            select(ScoringJob).where(
                ScoringJob.submission_id.in_([item.id for item in candidate_submissions])
            )
        ).all()
        assert len(jobs) == 2
        assert len({job.submission_id for job in jobs}) == 2


def test_hr_auth_override_and_retry_are_protected(backend):
    denied = backend.client.get("/api/v1/hr/roles")
    assert denied.status_code == 401
    _login_hr(backend)
    roles = backend.client.get("/api/v1/hr/roles")
    assert roles.status_code == 200
    assert len(roles.json()["data"]) == 5

    backend_role = next(
        role for role in roles.json()["data"] if role["name"] == "Backend Engineer"
    )
    candidate_page = backend.client.get(
        f"/api/v1/hr/roles/{backend_role['id']}/candidates?page_size=100"
    )
    candidate_summary = next(
        item
        for item in candidate_page.json()["data"]
        if item["external_id"] == "C2041"
    )
    assert candidate_summary["submissions"]
    discovered_submission_id = candidate_summary["submissions"][0]["submission_id"]
    discovered_detail = backend.client.get(
        f"/api/v1/hr/answers/{discovered_submission_id}"
    )
    assert discovered_detail.status_code == 200

    candidate_id = None
    with backend.session_factory() as session:
        candidate_id = session.scalar(
            select(Candidate.id).where(Candidate.external_id == "C2041")
        )
        submission = session.scalar(
            select(AnswerSubmission)
            .join(InterviewResponse, AnswerSubmission.response_id == InterviewResponse.id)
            .join(Question, InterviewResponse.question_id == Question.id)
            .where(
                AnswerSubmission.candidate_id == candidate_id,
                Question.external_id == "Q1",
                AnswerSubmission.is_current.is_(True),
            )
        )
        submission_id = submission.id
        job = session.scalar(
            select(ScoringJob).where(ScoringJob.submission_id == submission.id)
        )
        job.status = "failed"
        job.attempt_count = job.max_attempts
        job.last_error_message = "sanitized failure"
        job_id = job.id
        session.commit()

    invalid_review = backend.client.put(
        f"/api/v1/hr/answers/{submission_id}/review",
        json={"final_score": 4, "reason": "  "},
    )
    assert invalid_review.status_code == 422
    review = backend.client.put(
        f"/api/v1/hr/answers/{submission_id}/review",
        json={"final_score": 4, "reason": "Reviewed against the guide."},
    )
    assert review.status_code == 200
    assert review.json()["suggested_score"] is None
    assert review.json()["final_score"] == 4
    retried = backend.client.post(f"/api/v1/hr/scoring-jobs/{job_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    with backend.session_factory() as session:
        assert session.scalar(select(HrReview).where(HrReview.submission_id == submission_id))


def test_audio_upload_persists_and_hr_download_requires_auth(backend):
    interview = backend.client.get(
        "/api/v1/candidate/interview", headers=_candidate_headers(backend)
    ).json()
    q4 = next(question for question in interview["questions"] if question["external_id"] == "Q4")
    audio = b"\x00\x00\x00\x18ftypisomfake-audio-data"
    response = backend.client.post(
        f"/api/v1/candidate/answers/{q4['id']}/audio",
        headers={
            **_candidate_headers(backend),
            "Idempotency-Key": "audio-key-0001",
        },
        files={"file": ("response.m4a", audio, "audio/mp4")},
    )
    assert response.status_code == 201
    receipt = response.json()
    assert receipt["scoring_status"] == "needs_a_person"
    assert backend.client.post(
        f"/api/v1/candidate/answers/{q4['id']}/audio",
        headers={
            **_candidate_headers(backend),
            "Idempotency-Key": "audio-key-0001",
        },
        files={"file": ("response.m4a", audio, "audio/mp4")},
    ).status_code == 200
    detail_path = f"/api/v1/hr/answers/{receipt['submission_id']}"
    assert backend.client.get(detail_path).status_code == 401
    _login_hr(backend)
    detail = backend.client.get(detail_path)
    assert detail.status_code == 200
    assert detail.json()["audio_available"] is True
    assert detail.json()["suggested_score"] is None
    assert "transcript" in detail.json()["scoring_job"]["error_message"]
    assert backend.client.get(detail.json()["audio_download_url"]).content == audio
    assert backend.client.get(
        "/api/v1/candidate/interview",
        headers=_candidate_headers(backend),
    ).status_code == 200
