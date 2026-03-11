from aural_ingest.progress import ProgressEvent, emit, log


def test_progress_event_to_json_includes_optional_fields_only_when_present() -> None:
    e = ProgressEvent(type="stage_progress", id="x", progress=0.5)
    s = e.to_json()
    assert '"type": "stage_progress"' in s
    assert '"message"' not in s
    assert '"artifact"' not in s

    e2 = ProgressEvent(type="stage_done", id="x", progress=1.0, message="ok", artifact="a")
    s2 = e2.to_json()
    assert '"message": "ok"' in s2
    assert '"artifact": "a"' in s2


def test_emit_writes_jsonl_to_stdout(capsys) -> None:
    emit(ProgressEvent(type="stage_start", id="decode", progress=0.1))
    out = capsys.readouterr().out
    assert out.endswith("\n")
    assert '"id": "decode"' in out


def test_log_writes_to_stderr(capsys) -> None:
    log("hello")
    err = capsys.readouterr().err
    assert err == "hello\n"

