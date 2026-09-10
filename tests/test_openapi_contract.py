from __future__ import annotations

import copy
import io
import json
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

from app.main import create_app
from scripts.openapi_contract import (
    DEFAULT_CONTRACT_PATH,
    check_openapi_contract,
    format_openapi_json,
    generate_openapi_spec,
    main,
    update_openapi_contract,
    validate_openapi_spec,
    validate_operation_ids,
)


def test_canonical_artifact_exists() -> None:
    assert DEFAULT_CONTRACT_PATH.is_file()
    assert DEFAULT_CONTRACT_PATH.stat().st_size > 0


def test_canonical_artifact_is_valid_json() -> None:
    data = json.loads(DEFAULT_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert {"openapi", "info", "paths", "components"}.issubset(data.keys())


def test_canonical_artifact_conforms_to_openapi_3_1() -> None:
    data = json.loads(DEFAULT_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert data["openapi"] == "3.1.0"
    validate_openapi_spec(data)


def test_canonical_artifact_equals_live_application_openapi() -> None:
    live_spec = generate_openapi_spec(create_app())
    live_json = format_openapi_json(live_spec)
    canonical_json = DEFAULT_CONTRACT_PATH.read_text(encoding="utf-8")
    assert canonical_json == live_json


def test_generator_is_deterministic() -> None:
    app = create_app()
    first_json = format_openapi_json(generate_openapi_spec(app))
    for _ in range(5):
        assert first_json == format_openapi_json(generate_openapi_spec(app))


def test_offline_generation_without_server_or_database() -> None:
    spec = generate_openapi_spec()
    assert spec.get("openapi") == "3.1.0"
    assert len(spec.get("paths", {})) > 0


def test_routes_and_classifications_present() -> None:
    data = json.loads(DEFAULT_CONTRACT_PATH.read_text(encoding="utf-8"))
    paths = data.get("paths", {})

    expected_routes = [
        "/health",
        "/api/v1/me",
        "/api/v1/teams",
        "/api/v1/invitations/{token}",
        "/api/v1/invitations/{token}/accept",
        "/api/v1/teams/{team_id}/invitations/{id}/resend",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/assets",
    ]
    for route in expected_routes:
        assert route in paths

    tags_found: set[str] = set()
    for route_methods in paths.values():
        for operation in route_methods.values():
            if isinstance(operation, dict) and "tags" in operation:
                tags_found.update(operation["tags"])

    assert {"system", "users", "teams", "projects", "assets"}.issubset(tags_found)


def test_all_operation_ids_present_and_unique() -> None:
    spec = generate_openapi_spec()
    validate_operation_ids(spec)


def test_duplicate_operation_id_fails_validation() -> None:
    spec = generate_openapi_spec()
    bad_spec = copy.deepcopy(spec)
    paths = list(bad_spec["paths"].values())
    first_op = next(iter(paths[0].values()))
    second_op = next(iter(paths[1].values()))
    second_op["operationId"] = first_op["operationId"]

    with pytest.raises(ValueError, match="Duplicate operationId"):
        validate_operation_ids(bad_spec)


def test_missing_operation_id_fails_validation() -> None:
    spec = generate_openapi_spec()
    bad_spec = copy.deepcopy(spec)
    first_op = next(iter(next(iter(bad_spec["paths"].values())).values()))
    first_op["operationId"] = None

    with pytest.raises(ValueError, match="Missing operationId"):
        validate_operation_ids(bad_spec)


def test_generation_emits_no_warnings() -> None:
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        generate_openapi_spec()
        assert len(recorded) == 0


def test_cli_check_mode_success() -> None:
    code, message = check_openapi_contract(contract_path=DEFAULT_CONTRACT_PATH)
    assert code == 0
    assert "up to date" in message
    assert "conforms to OpenAPI 3.1" in message


def test_cli_update_mode_writes_artifact(tmp_path: Path) -> None:
    target_path = tmp_path / "contracts" / "openapi.json"
    result_path = update_openapi_contract(contract_path=target_path)

    assert result_path == target_path
    assert target_path.is_file()

    written_content = target_path.read_text(encoding="utf-8")
    parsed = json.loads(written_content)
    validate_openapi_spec(parsed)
    assert written_content == format_openapi_json(generate_openapi_spec())


def test_negative_drift_detection_and_actionable_diff(tmp_path: Path) -> None:
    drift_path = tmp_path / "drifted_openapi.json"
    drifted_spec = copy.deepcopy(generate_openapi_spec())
    drifted_spec["info"]["title"] = "Drifted Backend Title"

    drift_path.write_text(format_openapi_json(drifted_spec), encoding="utf-8")

    code, message = check_openapi_contract(contract_path=drift_path)
    assert code == 1
    assert "OpenAPI contract drift detected!" in message
    assert '-    "title": "Drifted Backend Title"' in message
    assert '+    "title": "VirtuJudge Backend"' in message
    assert "uv run python scripts/openapi_contract.py --update" in message


def test_missing_contract_file_fails_check(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing_openapi.json"
    code, message = check_openapi_contract(contract_path=missing_path)

    assert code == 1
    assert "not found" in message
    assert "uv run python scripts/openapi_contract.py --update" in message


def test_malformed_json_fails_check(tmp_path: Path) -> None:
    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text("{ incomplete json", encoding="utf-8")

    code, message = check_openapi_contract(contract_path=malformed_path)
    assert code == 1
    assert "not valid JSON" in message
    assert "uv run python scripts/openapi_contract.py --update" in message


def test_invalid_openapi_schema_fails_validation() -> None:
    bad_spec = {"openapi": "3.1.0", "paths": {}}
    with pytest.raises(OpenAPIValidationError):
        validate_openapi_spec(bad_spec)


def test_main_cli_check_success() -> None:
    with patch("sys.stdout", new_callable=io.StringIO) as stdout_mock:
        code = main(["--check"])
        assert code == 0
        assert "up to date" in stdout_mock.getvalue()


def test_main_cli_check_drift_fails(tmp_path: Path) -> None:
    drift_path = tmp_path / "drifted.json"
    drift_path.write_text("{}", encoding="utf-8")

    with patch("sys.stderr", new_callable=io.StringIO) as stderr_mock:
        code = main(["--check", "--contract-path", str(drift_path)])
        assert code == 1
        assert "failed" in stderr_mock.getvalue() or "drift" in stderr_mock.getvalue()


def test_main_cli_update_success(tmp_path: Path) -> None:
    target_path = tmp_path / "out.json"
    with patch("sys.stdout", new_callable=io.StringIO) as stdout_mock:
        code = main(["--update", "--contract-path", str(target_path)])
        assert code == 0
        assert "Updated canonical OpenAPI contract" in stdout_mock.getvalue()
    assert target_path.is_file()
