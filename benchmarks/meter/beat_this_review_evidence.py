from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GATE_ID = "beat_this_barline_listening_review"
MODEL_UPGRADE_EVIDENCE_ROOT_ENV = "AURAL_MODEL_UPGRADE_EVIDENCE_ROOT"
MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST = Path("benchmarks") / "runtime" / "model_upgrade_gate_evidence.md"
EVIDENCE_RELATIVE_PATH = "benchmarks/meter/beat_this_dbn_barline_listening_review.json"
TEMPLATE_RELATIVE_PATH = "benchmarks/meter/beat_this_dbn_barline_listening_review.template.json"
SMOKE_REPORT_RELATIVE_PATH = "benchmarks/meter/beat_this_dbn_refresh_meter_smoke.md"
REQUIRED_CASES: tuple[str, ...] = (
    "psalm_121_my_help.feedpak",
    "psalm_130_please_hear_me.feedpak",
    "psalm_5_every_morning.feedpak",
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def evidence_root() -> Path:
    raw = os.environ.get(MODEL_UPGRADE_EVIDENCE_ROOT_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST).is_file():
        return cwd
    return REPO_ROOT


def default_evidence_path() -> Path:
    return evidence_root() / EVIDENCE_RELATIVE_PATH


def default_template_path() -> Path:
    return evidence_root() / TEMPLATE_RELATIVE_PATH


def build_template(
    *,
    reviewed_by: str = "TODO",
    reviewed_at_utc: str = "TODO",
    approval_default: bool = False,
) -> dict[str, Any]:
    return {
        "version": 1,
        "gate": GATE_ID,
        "reviewed_by": reviewed_by,
        "reviewed_at_utc": reviewed_at_utc,
        "source_smoke_report": SMOKE_REPORT_RELATIVE_PATH,
        "cases": {
            case_id: {
                "barlines_ok": approval_default,
                "listening_ok": approval_default,
                "notes": "",
            }
            for case_id in REQUIRED_CASES
        },
    }


def _coerce_cases(raw_cases: Any) -> dict[str, Any] | None:
    if isinstance(raw_cases, dict):
        return raw_cases
    if not isinstance(raw_cases, list):
        return None

    cases: dict[str, Any] = {}
    for item in raw_cases:
        if not isinstance(item, dict):
            continue
        case_id = item.get("id") or item.get("pack") or item.get("name")
        if isinstance(case_id, str) and case_id.strip():
            cases[case_id.strip()] = item
    return cases


def _is_iso8601_utc_z_timestamp(value: str) -> bool:
    if "T" not in value or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(None)


def validate_payload(payload: Any) -> dict[str, Any]:
    status: dict[str, Any] = {
        "ready": False,
        "reviewed_cases": [],
        "missing_cases": list(REQUIRED_CASES),
        "errors": [],
        "warnings": [],
    }
    errors: list[str] = status["errors"]
    warnings: list[str] = status["warnings"]

    if not isinstance(payload, dict):
        errors.append("review evidence must be a JSON object")
        return status

    if payload.get("gate") != GATE_ID:
        errors.append(f"gate must be {GATE_ID!r}")
    if payload.get("version") != 1:
        errors.append("version must be 1")
    if not str(payload.get("reviewed_by") or "").strip() or payload.get("reviewed_by") == "TODO":
        errors.append("reviewed_by must identify the reviewer")
    reviewed_at_utc = str(payload.get("reviewed_at_utc") or "").strip()
    if not reviewed_at_utc or reviewed_at_utc == "TODO":
        errors.append("reviewed_at_utc must record the review timestamp")
    elif not _is_iso8601_utc_z_timestamp(reviewed_at_utc):
        errors.append("reviewed_at_utc must be an ISO-8601 UTC timestamp ending in Z")
    if payload.get("source_smoke_report") != SMOKE_REPORT_RELATIVE_PATH:
        errors.append(f"source_smoke_report must be {SMOKE_REPORT_RELATIVE_PATH!r}")

    cases = _coerce_cases(payload.get("cases"))
    if cases is None:
        errors.append("cases must be an object or list")
        return status

    reviewed: list[str] = []
    for case_id in REQUIRED_CASES:
        item = cases.get(case_id)
        if not isinstance(item, dict):
            errors.append(f"{case_id}: missing required case")
            continue
        missing_flags = [
            flag
            for flag in ("barlines_ok", "listening_ok")
            if item.get(flag) is not True
        ]
        if missing_flags:
            errors.append(f"{case_id}: {', '.join(missing_flags)} must be true")
            continue
        reviewed.append(case_id)

    status["reviewed_cases"] = reviewed
    status["missing_cases"] = [case_id for case_id in REQUIRED_CASES if case_id not in reviewed]
    status["ready"] = not errors
    return status


def validate_file(path: Path) -> dict[str, Any]:
    status: dict[str, Any] = {
        "path": str(path),
        "ready": False,
        "reviewed_cases": [],
        "missing_cases": list(REQUIRED_CASES),
        "errors": [],
        "warnings": [],
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        status["errors"].append(f"review evidence not found: {path}")
        return status
    except (OSError, json.JSONDecodeError) as exc:
        status["errors"].append(f"could not read review evidence: {exc}")
        return status

    payload_status = validate_payload(payload)
    payload_status["path"] = str(path)
    return payload_status


def write_template(path: Path, *, reviewed_by: str, reviewed_at_utc: str, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to overwrite it")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_template(reviewed_by=reviewed_by, reviewed_at_utc=reviewed_at_utc)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_review_evidence(
    path: Path,
    *,
    reviewed_by: str,
    reviewed_at_utc: str,
    approved_cases: list[str],
    force: bool = False,
) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to overwrite it")
    payload = build_template(reviewed_by=reviewed_by, reviewed_at_utc=reviewed_at_utc)
    unique_approved = set(approved_cases)
    unknown_cases = sorted(unique_approved.difference(REQUIRED_CASES))
    if unknown_cases:
        raise ValueError("unknown approved case(s): " + ", ".join(unknown_cases))
    for case_id in unique_approved:
        payload["cases"][case_id]["barlines_ok"] = True
        payload["cases"][case_id]["listening_ok"] = True

    status = validate_payload(payload)
    if not status["ready"]:
        details = "; ".join(status["errors"] or ["review evidence is incomplete"])
        raise ValueError(f"review evidence is not complete: {details}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def format_status(status: dict[str, Any]) -> str:
    lines = [
        f"path: {status.get('path', EVIDENCE_RELATIVE_PATH)}",
        f"ready: {str(bool(status.get('ready'))).lower()}",
        "reviewed_cases: " + ", ".join(status.get("reviewed_cases") or []),
        "missing_cases: " + ", ".join(status.get("missing_cases") or []),
    ]
    for warning in status.get("warnings") or []:
        lines.append(f"warning: {warning}")
    for error in status.get("errors") or []:
        lines.append(f"error: {error}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create or validate the Beat This DBN bar-line/listening review evidence "
            "used by aural_ingest runtime-check."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write-template",
        action="store_true",
        help=f"write a non-passing review template to {TEMPLATE_RELATIVE_PATH}",
    )
    mode.add_argument(
        "--validate",
        nargs="?",
        const="",
        metavar="PATH",
        help=f"validate PATH, defaulting to {EVIDENCE_RELATIVE_PATH}",
    )
    mode.add_argument(
        "--write-evidence",
        action="store_true",
        help=(
            f"write final passing review evidence to {EVIDENCE_RELATIVE_PATH}; "
            "requires reviewer metadata and --approve-case for every required case"
        ),
    )
    parser.add_argument("--output", default=None, help="template output path")
    parser.add_argument("--reviewed-by", default="TODO", help="reviewer name for the template")
    parser.add_argument(
        "--reviewed-at-utc",
        default="TODO",
        help="ISO-8601 UTC review timestamp ending in Z for the template",
    )
    parser.add_argument(
        "--approve-case",
        action="append",
        choices=REQUIRED_CASES,
        default=[],
        help="mark one required reviewed case as both barlines_ok and listening_ok",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing template")
    args = parser.parse_args(argv)

    if args.write_template:
        output = Path(args.output) if args.output else default_template_path()
        write_template(
            output,
            reviewed_by=args.reviewed_by,
            reviewed_at_utc=args.reviewed_at_utc,
            force=args.force,
        )
        print(f"wrote {output}")
        return 0

    if args.write_evidence:
        output = Path(args.output) if args.output else default_evidence_path()
        write_review_evidence(
            output,
            reviewed_by=args.reviewed_by,
            reviewed_at_utc=args.reviewed_at_utc,
            approved_cases=args.approve_case,
            force=args.force,
        )
        status = validate_file(output)
        print(format_status(status))
        return 0 if status["ready"] else 1

    if args.validate is not None:
        path = Path(args.validate) if args.validate else default_evidence_path()
        status = validate_file(path)
        print(format_status(status))
        return 0 if status["ready"] else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
