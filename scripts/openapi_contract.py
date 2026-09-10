from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

from openapi_spec_validator import validate
from openapi_spec_validator.exceptions import OpenAPISpecValidatorError
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.json"


def generate_openapi_spec(app: Any = None) -> dict[str, Any]:
    if app is None:
        from app.main import create_app

        app = create_app()

    spec: dict[str, Any] = app.openapi()
    return spec


def format_openapi_json(spec: dict[str, Any]) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def validate_operation_ids(spec: dict[str, Any]) -> None:
    seen_ids: set[str] = set()
    for path, operations in spec.get("paths", {}).items():
        if not isinstance(operations, dict):
            continue
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            if not op_id:
                raise ValueError(f"Missing operationId on {method.upper()} {path}")
            if op_id in seen_ids:
                raise ValueError(f"Duplicate operationId '{op_id}' on {method.upper()} {path}")
            seen_ids.add(op_id)


def validate_openapi_spec(spec: dict[str, Any]) -> None:
    validate(spec)
    validate_operation_ids(spec)


def compute_diff(
    expected_text: str,
    actual_text: str,
    expected_filename: str = "canonical",
    actual_filename: str = "generated",
) -> str:
    diff_lines = list(
        difflib.unified_diff(
            expected_text.splitlines(keepends=True),
            actual_text.splitlines(keepends=True),
            fromfile=expected_filename,
            tofile=actual_filename,
        )
    )
    return "".join(diff_lines)


def check_openapi_contract(
    contract_path: Path = DEFAULT_CONTRACT_PATH,
    app: Any = None,
) -> tuple[int, str]:
    resolved_path = contract_path.resolve()

    try:
        live_spec = generate_openapi_spec(app)
        validate_openapi_spec(live_spec)
    except (OpenAPIValidationError, OpenAPISpecValidatorError, ValueError) as exc:
        return 1, f"Live application OpenAPI specification failed validation: {exc}\n"
    except Exception as exc:
        return 1, f"Failed to generate OpenAPI specification from live application: {exc}\n"

    generated_json = format_openapi_json(live_spec)

    if not resolved_path.exists():
        rel_path = (
            resolved_path.relative_to(REPO_ROOT)
            if resolved_path.is_relative_to(REPO_ROOT)
            else resolved_path
        )
        return (
            1,
            f"Canonical OpenAPI contract file not found at '{rel_path}'.\n"
            "To generate it, run:\n"
            "  uv run python scripts/openapi_contract.py --update\n",
        )

    try:
        existing_json = resolved_path.read_text(encoding="utf-8")
        existing_spec: dict[str, Any] = json.loads(existing_json)
        validate_openapi_spec(existing_spec)
    except json.JSONDecodeError as exc:
        return (
            1,
            f"Checked-in OpenAPI contract at '{resolved_path}' is not valid JSON: {exc}\n"
            "To regenerate it, run:\n"
            "  uv run python scripts/openapi_contract.py --update\n",
        )
    except (OpenAPIValidationError, OpenAPISpecValidatorError, ValueError) as exc:
        return (
            1,
            f"Checked-in OpenAPI contract at '{resolved_path}' failed schema validation: {exc}\n"
            "To regenerate it, run:\n"
            "  uv run python scripts/openapi_contract.py --update\n",
        )
    except Exception as exc:
        return (
            1,
            f"Checked-in OpenAPI contract at '{resolved_path}' failed validation: {exc}\n"
            "To regenerate it, run:\n"
            "  uv run python scripts/openapi_contract.py --update\n",
        )

    if existing_json != generated_json:
        rel_path = (
            resolved_path.relative_to(REPO_ROOT)
            if resolved_path.is_relative_to(REPO_ROOT)
            else resolved_path
        )
        diff = compute_diff(
            existing_json,
            generated_json,
            expected_filename=str(rel_path),
            actual_filename="generated",
        )
        return (
            1,
            f"OpenAPI contract drift detected!\n\n"
            f"{diff}\n"
            "To update the canonical contract artifact, run:\n"
            "  uv run python scripts/openapi_contract.py --update\n",
        )

    rel_path = (
        resolved_path.relative_to(REPO_ROOT)
        if resolved_path.is_relative_to(REPO_ROOT)
        else resolved_path
    )
    return (
        0,
        f"OpenAPI contract at '{rel_path}' is up to date and conforms to OpenAPI 3.1.\n",
    )


def update_openapi_contract(
    contract_path: Path = DEFAULT_CONTRACT_PATH,
    app: Any = None,
) -> Path:
    resolved_path = contract_path.resolve()
    spec = generate_openapi_spec(app)
    validate_openapi_spec(spec)
    content = format_openapi_json(spec)

    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(content, encoding="utf-8")
    return resolved_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate, validate, and check drift for canonical OpenAPI contract."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--check",
        action="store_true",
        help="Check canonical OpenAPI contract for drift and schema validity (fails on drift)",
    )
    mode_group.add_argument(
        "--update",
        "--write",
        dest="update",
        action="store_true",
        help="Generate and update the canonical OpenAPI contract artifact",
    )
    parser.add_argument(
        "--contract-path",
        "--path",
        type=Path,
        default=DEFAULT_CONTRACT_PATH,
        help="Path to canonical contract artifact (default: contracts/openapi.json)",
    )

    args = parser.parse_args(argv)

    if args.check:
        code, message = check_openapi_contract(contract_path=args.contract_path)
        if code != 0:
            sys.stderr.write(message)
        else:
            sys.stdout.write(message)
        return code

    updated_path = update_openapi_contract(contract_path=args.contract_path)
    rel_path = (
        updated_path.relative_to(REPO_ROOT)
        if updated_path.is_relative_to(REPO_ROOT)
        else updated_path
    )
    sys.stdout.write(f"Updated canonical OpenAPI contract at '{rel_path}'.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
