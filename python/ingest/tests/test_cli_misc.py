import json
from pathlib import Path


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_parse_config_arg_variants(tmp_path: Path) -> None:
    from aural_ingest import cli

    assert cli._parse_config_arg(None) == {}
    assert cli._parse_config_arg('{"x": 1}') == {"x": 1}

    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"a": "b"}', encoding="utf-8")
    assert cli._parse_config_arg(str(cfg)) == {"a": "b"}


def test_cmd_stages_emits_all_stage_ids(capsys) -> None:
    from aural_ingest import cli

    rc = cli.cmd_stages(type("Args", (), {})())
    assert rc == 0

    out = capsys.readouterr().out.strip().splitlines()
    payloads = [json.loads(line) for line in out]
    ids = [p["id"] for p in payloads]
    assert ids == [s.id for s in cli.STAGES]


def test_cmd_info_missing_manifest_returns_error(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    args = type("Args", (), {})()
    args.songpack_dir = str(tmp_path / "missing.songpack")
    rc = cli.cmd_info(args)
    assert rc == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False


def test_cmd_info_returns_manifest_payload(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    songpack = tmp_path / "ok.songpack"
    songpack.mkdir(parents=True, exist_ok=True)
    manifest = {"song_id": "abc", "duration_sec": 12.3}
    _write_json(songpack / "manifest.json", manifest)

    args = type("Args", (), {})()
    args.songpack_dir = str(songpack)
    rc = cli.cmd_info(args)
    assert rc == 0

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["manifest"] == manifest


def test_cmd_validate_detects_invalid_notes_mid(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    songpack = tmp_path / "bad.songpack"
    (songpack / "audio").mkdir(parents=True, exist_ok=True)
    (songpack / "features").mkdir(parents=True, exist_ok=True)

    _write_json(songpack / "manifest.json", {"duration_sec": 10.0})
    (songpack / "audio" / "mix.wav").write_bytes(b"wav")
    (songpack / "features" / "notes.mid").write_bytes(b"not-midi")

    args = type("Args", (), {})()
    args.songpack_dir = str(songpack)
    rc = cli.cmd_validate(args)
    assert rc == 1

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is False
    assert "notes.mid" in payload["error"]


def test_cmd_import_returns_2_for_missing_input(tmp_path: Path) -> None:
    from aural_ingest import cli

    args = type("Args", (), {})()
    args.input_audio_path = str(tmp_path / "nope.wav")
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import(args) == 2


def test_build_parser_knows_core_commands() -> None:
    from aural_ingest import cli

    p = cli.build_parser()
    assert p.parse_args(["stages"]).cmd == "stages"
    assert p.parse_args(["validate", "x"]).cmd == "validate"
    assert p.parse_args(["info", "x"]).cmd == "info"
    assert p.parse_args(["benchmark-drums", "stem.wav", "reference.json"]).cmd == "benchmark-drums"
    assert p.parse_args(["runtime-check"]).cmd == "runtime-check"
    assert p.parse_args(["import", "in.wav", "--out", "o.songpack"]).cmd == "import"
    assert p.parse_args(["import-dir", "in_dir", "--out", "o.songpack"]).cmd == "import-dir"
    assert p.parse_args(["import-dtx", "chart.dtx", "--out", "o.songpack"]).cmd == "import-dtx"

    parsed = p.parse_args(
        [
            "import",
            "in.wav",
            "--out",
            "o.songpack",
            "--drum-filter",
            "combined_filter",
            "--melodic-method",
            "pyin",
            "--shifts",
            "2",
            "--multi-filter",
        ]
    )
    assert parsed.drum_filter == "combined_filter"
    assert parsed.melodic_method == "pyin"
    assert parsed.shifts == 2
    assert parsed.multi_filter is True


def test_cmd_benchmark_drums_emits_json_payload(tmp_path: Path, monkeypatch, capsys) -> None:
    from aural_ingest import cli
    from aural_ingest.transcription import DrumEvent

    stem = tmp_path / "stem.wav"
    stem.write_bytes(b"x")
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps([{"t": 0.5, "class": "snare"}]), encoding="utf-8")

    monkeypatch.setattr(
        cli,
        "build_default_drum_algorithm_registry",
        lambda: {
            "combined_filter": lambda _stem: [DrumEvent(time=0.5, note=38, velocity=100)],
            "adaptive_beat_grid": lambda _stem: [DrumEvent(time=0.52, note=50, velocity=100)],
        },
    )

    rc = cli.main(
        [
            "benchmark-drums",
            str(stem),
            str(reference),
            "--algorithm",
            "combined_filter",
            "--algorithm",
            "adaptive_beat_grid",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["class_order"] == ["kick", "snare", "hi_hat", "crash", "ride", "tom1", "tom2", "tom3"]

    results = {result["algorithm"]: result for result in payload["results"]}
    assert results["combined_filter"]["per_class"]["snare"]["tp"] == 1
    assert results["adaptive_beat_grid"]["per_class"]["snare"]["fn"] == 1
    assert results["adaptive_beat_grid"]["confusions"] == [
        {"reference_class": "snare", "predicted_class": "tom1", "count": 1}
    ]


def test_cmd_runtime_check_emits_json_payload(monkeypatch, capsys) -> None:
    from aural_ingest import cli

    seen = {}

    def fake_collect(config: dict[str, object], *, load_model: bool) -> dict[str, object]:
        seen["config"] = config
        seen["load_model"] = load_model
        return {
            "python_executable": "D:/AuralPrimer/python/ingest/.venv/Scripts/python.exe",
            "torch_version": "2.11.0",
            "torchaudio_version": "2.11.0",
            "demucs_version": "4.0.1",
            "modelpack": {
                "id": "demucs_6",
                "version": "htdemucs_6s",
            },
            "model": {
                "sources": ["drums", "bass", "guitar", "keys", "vocals", "other"],
            },
        }

    monkeypatch.setattr(cli, "_collect_demucs_runtime_status", fake_collect)

    rc = cli.main(
        [
            "runtime-check",
            "--json",
            "--demucs-modelpack-zip-path",
            "D:/AuralPrimer/modelpacks/demucs_6.zip",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["runtime"]["torch_version"] == "2.11.0"
    assert payload["runtime"]["modelpack"]["id"] == "demucs_6"
    assert seen == {
        "config": {"demucs_modelpack_zip_path": "D:/AuralPrimer/modelpacks/demucs_6.zip"},
        "load_model": True,
    }


def test_main_runs_stages_command(capsys) -> None:
    from aural_ingest import cli

    rc = cli.main(["stages"])
    assert rc == 0
    assert '"id"' in capsys.readouterr().out


def test_generation_helpers_cover_edge_cases() -> None:
    from aural_ingest import cli

    beats = cli._generate_beats(0.0, -1.0)
    assert len(beats) == 1
    assert beats[0]["t"] == 0.0

    sections = cli._generate_sections(0.0, -1.0)
    assert len(sections) == 1
    assert sections[0]["t0"] == 0.0
    assert sections[0]["t1"] == 0.0


def test_cmd_import_dir_returns_2_for_missing_input_dir(tmp_path: Path) -> None:
    from aural_ingest import cli

    args = type("Args", (), {})()
    args.input_dir_path = str(tmp_path / "no_such")
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import_dir(args) == 2


def test_find_audio_source_prefers_mix_files(tmp_path: Path) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "z.wav").write_bytes(b"x")
    (src_dir / "mix.wav").write_bytes(b"x")

    assert cli._find_audio_source_in_dir(src_dir) == src_dir / "mix.wav"


def test_find_audio_source_falls_back_to_sorted_recursive_match(tmp_path: Path) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    (src_dir / "b").mkdir(parents=True, exist_ok=True)
    (src_dir / "a").mkdir(parents=True, exist_ok=True)
    (src_dir / "b" / "track.mp3").write_bytes(b"x")
    (src_dir / "a" / "track.ogg").write_bytes(b"x")

    # sorted by relative path => a/track.ogg comes first
    assert cli._find_audio_source_in_dir(src_dir) == src_dir / "a" / "track.ogg"


def test_cmd_import_dir_returns_2_when_no_supported_audio_found(tmp_path: Path) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "readme.txt").write_text("x", encoding="utf-8")

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import_dir(args) == 2


def test_cmd_import_dir_forwards_selected_source_to_cmd_import(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "mix.wav"
    src.write_bytes(b"x")

    seen = {}

    def fake_cmd_import(args):
        seen["input_audio_path"] = args.input_audio_path
        seen["out"] = args.out
        seen["profile"] = args.profile
        return 0

    monkeypatch.setattr(cli, "cmd_import", fake_cmd_import)

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = "{}"
    args.title = "t"
    args.artist = "a"
    args.duration_sec = None

    assert cli.cmd_import_dir(args) == 0
    assert seen["input_audio_path"] == str(src)
    assert seen["out"] == str(tmp_path / "x.songpack")
    assert seen["profile"] == "full"


def test_cmd_import_dtx_returns_2_for_missing_dtx_file(tmp_path: Path) -> None:
    from aural_ingest import cli

    args = type("Args", (), {})()
    args.dtx_path = str(tmp_path / "nope.dtx")
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import_dtx(args) == 2


def test_find_audio_source_for_dtx_prefers_referenced_files(tmp_path: Path) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "song"
    src_dir.mkdir(parents=True, exist_ok=True)
    dtx = src_dir / "chart.dtx"
    (src_dir / "audio").mkdir(parents=True, exist_ok=True)
    ref_audio = src_dir / "audio" / "drums.wav"
    ref_audio.write_bytes(b"x")
    # fallback mix file should not be chosen when referenced audio exists
    (src_dir / "mix.wav").write_bytes(b"x")

    dtx.write_text("#WAV01 audio/drums.wav\n", encoding="utf-8")

    assert cli._find_audio_source_for_dtx(dtx) == ref_audio


def test_cmd_import_dtx_forwards_selected_audio_to_cmd_import(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "song"
    src_dir.mkdir(parents=True, exist_ok=True)
    dtx = src_dir / "chart.dtx"
    wav = src_dir / "mix.wav"
    wav.write_bytes(b"x")
    dtx.write_text("; minimal dtx\n", encoding="utf-8")

    seen = {}

    def fake_cmd_import(args):
        seen["input_audio_path"] = args.input_audio_path
        seen["title"] = args.title
        return 0

    monkeypatch.setattr(cli, "cmd_import", fake_cmd_import)

    args = type("Args", (), {})()
    args.dtx_path = str(dtx)
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import_dtx(args) == 0
    assert seen["input_audio_path"] == str(wav)
    # default title should use dtx stem when not provided
    assert seen["title"] == "chart"


def test_cmd_import_handles_unknown_drum_filter_and_rejects_other_invalid_transcription_options(tmp_path: Path) -> None:
    from aural_ingest import cli

    src = tmp_path / "in.wav"
    src.write_bytes(b"RIFFxxxxWAVE")

    args = type("Args", (), {})()
    args.input_audio_path = str(src)
    args.out = str(tmp_path / "out.songpack")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None
    args.drum_filter = "not_valid"
    args.melodic_method = "auto"
    args.shifts = 1
    args.multi_filter = False

    # Unknown drum filter is accepted and normalized by recovery policy.
    tr_opts, tr_err = cli._resolve_transcription_options(args)
    assert tr_err is None
    assert tr_opts is not None
    assert tr_opts["drum_filter"] == "adaptive_beat_grid"
    assert tr_opts["warnings"]

    args.drum_filter = "combined_filter"
    args.melodic_method = "not_valid"
    assert cli.cmd_import(args) == 2

    args.melodic_method = "auto"
    args.shifts = 0
    assert cli.cmd_import(args) == 2


def test_cmd_import_dir_forwards_transcription_options(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "mix.wav"
    src.write_bytes(b"x")

    seen = {}

    def fake_cmd_import(args):
        seen["drum_filter"] = args.drum_filter
        seen["melodic_method"] = args.melodic_method
        seen["shifts"] = args.shifts
        seen["multi_filter"] = args.multi_filter
        return 0

    monkeypatch.setattr(cli, "cmd_import", fake_cmd_import)

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = "{}"
    args.title = "t"
    args.artist = "a"
    args.duration_sec = None
    args.drum_filter = "dsp_bandpass_improved"
    args.melodic_method = "basic_pitch"
    args.shifts = 3
    args.multi_filter = True

    assert cli.cmd_import_dir(args) == 0
    assert seen == {
        "drum_filter": "dsp_bandpass_improved",
        "melodic_method": "basic_pitch",
        "shifts": 3,
        "multi_filter": True,
    }
