from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints


class ErrorResponse(BaseModel):
    code: str
    message: str


class CandidateQuestion(BaseModel):
    id: int
    external_id: str
    position: int
    question_text: str


class CandidateProgress(BaseModel):
    current_question_external_id: str | None
    submitted_question_ids: list[str]


class CandidateInterview(BaseModel):
    candidate: dict[str, str]
    questions: list[CandidateQuestion]
    progress: CandidateProgress


class AnswerSubmissionRequest(BaseModel):
    question_id: Annotated[str, StringConstraints(min_length=1, max_length=16)]
    answer_text: Annotated[str, StringConstraints(min_length=1, max_length=20000)]


class AnswerSubmissionReceipt(BaseModel):
    submission_id: int
    question_id: str
    received: StrictBool = True
    scoring_status: str
    version: int
    current: bool


class HrLoginRequest(BaseModel):
    username: str
    password: str


class HrReviewRequest(BaseModel):
    final_score: Annotated[StrictInt, Field(ge=1, le=4)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ScoringResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    score: Annotated[StrictInt, Field(ge=1, le=4)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    needs_a_person: StrictBool
    review_reason: Annotated[
        str | None, Field(max_length=2000)
    ]
