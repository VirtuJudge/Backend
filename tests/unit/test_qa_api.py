import json

from starlette.requests import Request

from app.api.routes.qa import _qa_error
from app.domain.session_workflow.exceptions import AnswerDurationExceeded
from app.main import create_app
from app.settings import Settings


def test_openapi_exposes_qa_round_and_answer_commands() -> None:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    paths = app.openapi()["paths"]

    assert paths["/api/v1/practice-sessions/{session_id}/qa"]["get"]["responses"]["200"]
    assert paths["/api/v1/questions/{question_id}/answer-upload-intents"]["post"]["responses"][
        "201"
    ]
    assert paths["/api/v1/answers/{answer_id}/submit"]["post"]["responses"]["202"]
    assert paths["/api/v1/questions/{question_id}/skip"]["post"]["responses"]["200"]


def test_qa_commands_require_idempotency_keys() -> None:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    paths = app.openapi()["paths"]

    for path in (
        "/api/v1/questions/{question_id}/answer-upload-intents",
        "/api/v1/answers/{answer_id}/submit",
        "/api/v1/questions/{question_id}/skip",
    ):
        parameters = paths[path]["post"]["parameters"]
        idempotency = next(item for item in parameters if item["name"] == "Idempotency-Key")
        assert idempotency["required"] is True


def test_qa_openapi_documents_problem_details_errors() -> None:
    app = create_app(Settings(app_env="test", database_url="sqlite+aiosqlite:///:memory:"))
    paths = app.openapi()["paths"]

    for path, method in (
        ("/api/v1/practice-sessions/{session_id}/qa", "get"),
        ("/api/v1/questions/{question_id}/answer-upload-intents", "post"),
        ("/api/v1/answers/{answer_id}/submit", "post"),
        ("/api/v1/questions/{question_id}/skip", "post"),
    ):
        responses = paths[path][method]["responses"]
        assert "403" in responses
        assert "404" in responses
        assert "409" in responses
        problem = responses["409"]["content"]["application/problem+json"]
        assert problem["schema"]


def test_overlong_answer_uses_documented_problem_code() -> None:
    request = Request(
        {"type": "http", "method": "POST", "path": "/answers/a/submit", "headers": []}
    )

    response = _qa_error(AnswerDurationExceeded("too long"), request)

    assert response.status_code == 422
    assert json.loads(response.body)["code"] == "duration_exceeded"
