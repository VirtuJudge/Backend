from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


class ContextualValidationError(Exception):
    def __init__(self, message: str, path: str) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


def format_json_path(path_parts: list[Any]) -> str:
    if not path_parts:
        return ""
    return "/" + "/".join(str(p) for p in path_parts)


def collect_errors(
    err: ValidationError, parent_parts: list[Any] | None = None
) -> list[tuple[str, str]]:
    if parent_parts is None:
        parent_parts = []
    cur_parts = parent_parts + list(err.path)
    res = [(format_json_path(cur_parts), err.message)]
    for ctx in err.context:
        res.extend(collect_errors(ctx, cur_parts))
    return res


def validate_schema_meta(schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


def validate_contextual_rules(data: dict[str, Any], context: dict[str, Any]) -> None:
    if "current_attempt" in context:
        current_attempt = int(context["current_attempt"])
        attempt = data.get("analysis_attempt")
        if attempt is not None and int(attempt) < current_attempt:
            raise ContextualValidationError(
                f"attempt {attempt} is stale; current attempt is {current_attempt}",
                path="/analysis_attempt",
            )

    if "current_sequence" in context:
        current_seq = int(context["current_sequence"])
        seq = data.get("sequence")
        if seq is not None and int(seq) <= current_seq:
            raise ContextualValidationError(
                f"sequence {seq} is not greater than currently processed sequence {current_seq}",
                path="/sequence",
            )

    if "expected_practice_session_id" in context:
        expected_session = str(context["expected_practice_session_id"])
        actual_session = str(data.get("practice_session_id", ""))
        if actual_session != expected_session:
            raise ContextualValidationError(
                "practice_session_id does not match expected session ancestry",
                path="/practice_session_id",
            )

    if "expected_trace_id" in context:
        expected_trace = str(context["expected_trace_id"])
        actual_trace = str(data.get("trace_id", ""))
        if actual_trace != expected_trace:
            raise ContextualValidationError(
                "trace_id does not correlate with dispatched job trace_id",
                path="/trace_id",
            )


def validate_fixture_entry(
    fixture_def: dict[str, Any],
    schemas: dict[str, Any],
    root_dir: Path,
) -> tuple[bool, str]:
    fixture_id = fixture_def["id"]
    rel_path = fixture_def["path"]
    schema_key = fixture_def["schema"]
    is_valid_expected = fixture_def["valid"]
    validation_level = fixture_def.get("validation_level", "schema")

    fixture_file = root_dir / rel_path
    if not fixture_file.is_file():
        return False, f"Fixture file not found: {fixture_file}"

    try:
        data = json.loads(fixture_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        return False, f"Invalid JSON in {fixture_file}: {err}"

    if schema_key not in schemas:
        schema_file = root_dir / schema_key
        if not schema_file.is_file():
            return False, f"Schema file not found: {schema_file}"
        schema_dict = json.loads(schema_file.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema_dict)
        schemas[schema_key] = schema_dict

    schema = schemas[schema_key]
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)

    expected_error = fixture_def.get("expected_error", {})
    expected_path = expected_error.get("path")
    expected_message = expected_error.get("message")

    if validation_level == "schema":
        if is_valid_expected:
            try:
                validator.validate(data)
                return True, f"{fixture_id}: valid as expected"
            except ValidationError as err:
                path = format_json_path(list(err.path))
                return False, f"{fixture_id}: expected valid, but failed at '{path}': {err.message}"
        else:
            errors = list(validator.iter_errors(data))
            if not errors:
                return False, f"{fixture_id}: expected validation failure, but payload passed"

            all_errors: list[tuple[str, str]] = []
            for val_err in errors:
                all_errors.extend(collect_errors(val_err))

            matching_errors = all_errors
            if expected_path is not None:
                matching_errors = [
                    (p, m)
                    for p, m in matching_errors
                    if p == expected_path or p.startswith(expected_path)
                ]
            if expected_message is not None:
                matching_errors = [
                    (p, m) for p, m in matching_errors if expected_message.lower() in m.lower()
                ]

            if not matching_errors:
                first_path, first_msg = all_errors[0]
                return (
                    False,
                    f"{fixture_id}: path/message mismatch: expected path '{expected_path}' "
                    f"and message '{expected_message}', but actual errors were: {all_errors[:3]}",
                )

            matched_path, matched_msg = matching_errors[0]
            return True, f"{fixture_id}: rejected as expected at '{matched_path}' ({matched_msg})"

    elif validation_level == "contextual":
        try:
            validator.validate(data)
        except ValidationError as err:
            path = format_json_path(list(err.path))
            return False, f"{fixture_id}: structurally invalid at '{path}': {err.message}"

        context = fixture_def.get("context", {})
        try:
            validate_contextual_rules(data, context)
            if is_valid_expected:
                return True, f"{fixture_id}: valid in context"
            return False, f"{fixture_id}: expected contextual rejection, but passed"
        except ContextualValidationError as err:
            if not is_valid_expected:
                if expected_path is not None and err.path != expected_path:
                    return (
                        False,
                        (
                            f"{fixture_id}: path mismatch: expected '{expected_path}', "
                            f"got '{err.path}'"
                        ),
                    )
                if (
                    expected_message is not None
                    and expected_message.lower() not in err.message.lower()
                ):
                    return (
                        False,
                        (
                            f"{fixture_id}: message mismatch: expected '{expected_message}', "
                            f"got '{err.message}'"
                        ),
                    )
                return (
                    True,
                    (
                        f"{fixture_id}: contextual rejection as expected "
                        f"at '{err.path}' ({err.message})"
                    ),
                )
            return (
                False,
                f"{fixture_id}: unexpected contextual failure at '{err.path}': {err.message}",
            )

    return False, f"{fixture_id}: unknown validation level '{validation_level}'"


def run_contract_checks(
    schemas_dir: Path,
    manifest_path: Path,
    root_dir: Path,
    verbose: bool = False,
) -> tuple[bool, list[str]]:
    messages: list[str] = []
    success = True

    for schema_file in sorted(schemas_dir.glob("*.json")):
        try:
            validate_schema_meta(schema_file)
            messages.append(f"Schema {schema_file.name} conforms to Draft 2020-12 meta-schema")
        except Exception as err:
            success = False
            messages.append(f"Schema {schema_file.name} failed meta-schema check: {err}")

    if not manifest_path.is_file():
        return False, [f"Manifest not found: {manifest_path}"]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        return False, [f"Invalid manifest JSON: {err}"]

    schemas: dict[str, Any] = {}
    fixtures = manifest.get("fixtures", [])
    if not fixtures:
        return False, ["Manifest contains no fixtures"]

    passed_count = 0
    for fixture_def in fixtures:
        ok, msg = validate_fixture_entry(fixture_def, schemas, root_dir)
        if ok:
            passed_count += 1
            if verbose:
                messages.append(f"  [PASS] {msg}")
        else:
            success = False
            messages.append(f"  [FAIL] {msg}")

    summary = f"{passed_count}/{len(fixtures)} AI contract fixtures passed"
    messages.append(summary)
    return success, messages


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate AI contract schemas and golden fixtures."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run validation and exit non-zero on failure",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output for each fixture",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    schemas_dir = repo_root / "contracts" / "schemas"
    manifest_path = repo_root / "contracts" / "fixtures" / "ai" / "manifest.json"

    ok, messages = run_contract_checks(schemas_dir, manifest_path, repo_root, verbose=args.verbose)
    for msg in messages:
        print(msg)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
