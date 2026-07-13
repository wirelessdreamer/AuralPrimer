import importlib.util
import json
from pathlib import Path


def _load_helper():
    script_path = (
        Path(__file__).resolve().parents[3]
        / "benchmarks"
        / "meter"
        / "beat_this_review_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("_test_beat_this_review_evidence", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_beat_this_review_helper_matches_runtime_gate_contract() -> None:
    from aural_ingest import cli

    helper = _load_helper()

    assert helper.REQUIRED_CASES == cli.BEAT_THIS_REVIEW_REQUIRED_CASES
    assert helper.EVIDENCE_RELATIVE_PATH == cli.BEAT_THIS_REVIEW_EVIDENCE_RELATIVE_PATH.as_posix()
    assert helper.MODEL_UPGRADE_EVIDENCE_ROOT_ENV == cli.MODEL_UPGRADE_EVIDENCE_ROOT_ENV
    assert helper.MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST == cli.MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST


def test_beat_this_review_template_is_non_passing_until_reviewed() -> None:
    helper = _load_helper()
    template_path = Path(__file__).resolve().parents[3] / helper.TEMPLATE_RELATIVE_PATH
    payload = json.loads(template_path.read_text(encoding="utf-8"))

    status = helper.validate_payload(payload)

    assert status["ready"] is False
    assert status["reviewed_cases"] == []
    assert status["missing_cases"] == list(helper.REQUIRED_CASES)
    for case_id in helper.REQUIRED_CASES:
        assert f"{case_id}: barlines_ok, listening_ok must be true" in status["errors"]
    assert "reviewed_by must identify the reviewer" in status["errors"]
    assert "reviewed_at_utc must record the review timestamp" in status["errors"]


def test_beat_this_review_helper_accepts_runtime_ready_payload() -> None:
    helper = _load_helper()
    payload = helper.build_template(
        reviewed_by="test-reviewer",
        reviewed_at_utc="2026-07-08T00:00:00Z",
        approval_default=True,
    )

    status = helper.validate_payload(payload)

    assert status["ready"] is True
    assert status["reviewed_cases"] == list(helper.REQUIRED_CASES)
    assert status["missing_cases"] == []
    assert status["errors"] == []


def test_beat_this_review_helper_rejects_unreviewed_metadata_even_when_approved() -> None:
    helper = _load_helper()
    payload = helper.build_template(approval_default=True)

    status = helper.validate_payload(payload)

    assert status["ready"] is False
    assert status["reviewed_cases"] == list(helper.REQUIRED_CASES)
    assert status["missing_cases"] == []
    assert "reviewed_by must identify the reviewer" in status["errors"]
    assert "reviewed_at_utc must record the review timestamp" in status["errors"]


def test_beat_this_review_helper_rejects_non_utc_review_timestamp() -> None:
    helper = _load_helper()
    payload = helper.build_template(
        reviewed_by="test-reviewer",
        reviewed_at_utc="yesterday",
        approval_default=True,
    )

    status = helper.validate_payload(payload)

    assert status["ready"] is False
    assert status["reviewed_cases"] == list(helper.REQUIRED_CASES)
    assert status["missing_cases"] == []
    assert "reviewed_at_utc must be an ISO-8601 UTC timestamp ending in Z" in status["errors"]


def test_beat_this_review_helper_writes_template_without_gate_path(tmp_path: Path) -> None:
    helper = _load_helper()
    output = tmp_path / "review.template.json"

    helper.write_template(output, reviewed_by="reviewer", reviewed_at_utc="2026-07-08T00:00:00Z")

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert output.name != Path(helper.EVIDENCE_RELATIVE_PATH).name
    assert payload["reviewed_by"] == "reviewer"
    assert set(payload["cases"]) == set(helper.REQUIRED_CASES)
    assert helper.validate_payload(payload)["ready"] is False


def test_beat_this_review_helper_refuses_incomplete_final_evidence(tmp_path: Path) -> None:
    helper = _load_helper()
    output = tmp_path / "review.json"

    try:
        helper.write_review_evidence(
            output,
            reviewed_by="test-reviewer",
            reviewed_at_utc="2026-07-08T00:00:00Z",
            approved_cases=[helper.REQUIRED_CASES[0]],
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected incomplete final evidence to be rejected")

    assert not output.exists()
    assert "review evidence is not complete" in message
    assert f"{helper.REQUIRED_CASES[1]}: barlines_ok, listening_ok must be true" in message


def test_beat_this_review_helper_writes_complete_final_evidence(tmp_path: Path) -> None:
    helper = _load_helper()
    output = tmp_path / "review.json"

    helper.write_review_evidence(
        output,
        reviewed_by="test-reviewer",
        reviewed_at_utc="2026-07-08T00:00:00Z",
        approved_cases=list(helper.REQUIRED_CASES),
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    status = helper.validate_payload(payload)
    assert status["ready"] is True
    assert status["reviewed_cases"] == list(helper.REQUIRED_CASES)
    assert all(item["barlines_ok"] and item["listening_ok"] for item in payload["cases"].values())


def test_beat_this_review_helper_cli_writes_complete_final_evidence(tmp_path: Path) -> None:
    helper = _load_helper()
    output = tmp_path / "review.json"

    rc = helper.main(
        [
            "--write-evidence",
            "--output",
            str(output),
            "--reviewed-by",
            "test-reviewer",
            "--reviewed-at-utc",
            "2026-07-08T00:00:00Z",
            *sum((["--approve-case", case_id] for case_id in helper.REQUIRED_CASES), []),
        ]
    )

    assert rc == 0
    assert helper.validate_file(output)["ready"] is True


def test_beat_this_review_helper_defaults_to_model_upgrade_evidence_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    helper = _load_helper()
    evidence_root = tmp_path / "evidence-root"
    monkeypatch.setenv(helper.MODEL_UPGRADE_EVIDENCE_ROOT_ENV, str(evidence_root))

    assert helper.default_evidence_path() == evidence_root.resolve() / helper.EVIDENCE_RELATIVE_PATH
    assert helper.default_template_path() == evidence_root.resolve() / helper.TEMPLATE_RELATIVE_PATH

    assert helper.main(["--write-template"]) == 0
    template = evidence_root / helper.TEMPLATE_RELATIVE_PATH
    assert template.is_file()

    payload = helper.build_template(
        reviewed_by="test-reviewer",
        reviewed_at_utc="2026-07-08T00:00:00Z",
        approval_default=True,
    )
    evidence = evidence_root / helper.EVIDENCE_RELATIVE_PATH
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    assert helper.main(["--validate"]) == 0


def test_beat_this_review_helper_uses_runtime_check_evidence_root_fallbacks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    helper = _load_helper()
    env_root = tmp_path / "env-root"
    cwd_root = tmp_path / "cwd-root"
    outside_root = tmp_path / "outside"
    checklist = cwd_root / helper.MODEL_UPGRADE_GATE_EVIDENCE_CHECKLIST
    checklist.parent.mkdir(parents=True)
    checklist.write_text("# gate evidence\n", encoding="utf-8")
    outside_root.mkdir()

    monkeypatch.setenv(helper.MODEL_UPGRADE_EVIDENCE_ROOT_ENV, str(env_root))
    assert helper.evidence_root() == env_root.resolve()
    assert helper.default_evidence_path() == env_root.resolve() / helper.EVIDENCE_RELATIVE_PATH

    monkeypatch.delenv(helper.MODEL_UPGRADE_EVIDENCE_ROOT_ENV, raising=False)
    monkeypatch.chdir(cwd_root)
    assert helper.evidence_root() == cwd_root.resolve()
    assert helper.default_template_path() == cwd_root.resolve() / helper.TEMPLATE_RELATIVE_PATH

    monkeypatch.setenv(helper.MODEL_UPGRADE_EVIDENCE_ROOT_ENV, " ")
    monkeypatch.chdir(outside_root)
    assert helper.evidence_root() == helper.REPO_ROOT
