import importlib.machinery
import json
from pathlib import Path
import sys
import struct
import types
import wave


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _write_mono_wav(path: Path, samples: list[float], sr: int = 48_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sr)
        for sample in samples:
            v = max(-1.0, min(1.0, float(sample)))
            wav_file.writeframesraw(struct.pack("<h", int(v * 32767.0)))


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
    transcribe_stage = next(p for p in payloads if p["id"] == "transcribe_drums")
    assert "mr_mt3_drums" in transcribe_stage["variants"]
    assert transcribe_stage["variants"]["mr_mt3_drums"]["required_models"][0]["modelpack_id"] == "mr_mt3"
    separate_stage = next(p for p in payloads if p["id"] == "separate_stems")
    assert separate_stage["required_models"][0]["modelpack_id"] == "demucs_6"
    assert separate_stage["policy"]["demucs_support_status"] == "optional_experimental"
    beats_stage = next(p for p in payloads if p["id"] == "beats_tempo")
    assert beats_stage["policy"]["production_default"] == "librosa_first"
    assert beats_stage["policy"]["fallback_mode"] == "standard"


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


def test_cmd_audit_drums_reports_stem_energy_by_lane(tmp_path: Path, capsys) -> None:
    from aural_ingest import cli

    songpack = tmp_path / "Psalm.songpack"
    samples = [0.0] * 48_000
    for idx in range(24_000, 24_600):
        samples[idx] = 0.5

    _write_mono_wav(songpack / "audio" / "stems" / "drums.wav", samples)
    _write_json(
        songpack / "manifest.json",
        {
            "title": "Psalm Fixture",
            "profile": "full",
            "pipeline": {
                "transcription": {
                    "transcription_profile": "gameplay_default",
                    "drum_source_kind": "separated_drums",
                    "drum_filter": "combined_filter",
                    "drum_filter_used": "combined_filter",
                    "drum_silence_gate": {"events_in": 2, "events_out": 1, "dropped": 1},
                }
            },
        },
    )
    _write_json(
        songpack / "features" / "events.json",
        {
            "onsets": [
                {"t": 0.1, "note": 38, "velocity": 90, "instrument": "drums"},
                {"t": 0.5, "note": 36, "velocity": 100, "instrument": "drums"},
            ]
        },
    )

    args = type("Args", (), {})()
    args.songpack_dir = str(songpack)
    args.window_ms = 20.0
    args.threshold_dbfs = None

    assert cli.cmd_audit_drums(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["manifest"]["stem_silence_gate_present"] is True
    assert payload["manifest"]["stem_silence_gate"]["dropped"] == 1
    assert payload["events"]["count"] == 2
    assert payload["events"]["by_lane"] == {"kick": 1, "snare": 1}
    assert payload["stem_energy"]["available"] is True
    assert payload["stem_energy"]["below_thresholds"]["-50"]["count"] == 1
    assert payload["stem_energy"]["below_thresholds"]["-50"]["by_lane"] == {"snare": 1}


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
    assert p.parse_args(["audit-drums", "x"]).cmd == "audit-drums"
    assert p.parse_args(["runtime-check"]).cmd == "runtime-check"
    assert p.parse_args(["benchmark-drums", "stem.wav", "reference.json"]).cmd == "benchmark-drums"
    assert p.parse_args(["refine-piano", "--audio", "keys.wav", "--source-midi", "suno.mid"]).cmd == "refine-piano"
    assert p.parse_args(["import", "in.wav", "--out", "o.songpack"]).cmd == "import"
    assert p.parse_args(["import-dir", "in_dir", "--out", "o.songpack"]).cmd == "import-dir"
    assert p.parse_args(["import-unsupported_chart_format", "chart.unsupported_chart_format", "--out", "o.songpack"]).cmd == "import-unsupported_chart_format"

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
            "--beat-analysis-mode",
            "high_accuracy",
            "--stem-separation-provider",
            "none",
            "--shifts",
            "2",
            "--multi-filter",
        ]
    )
    assert parsed.drum_filter == "combined_filter"
    assert parsed.melodic_method == "pyin"
    assert parsed.beat_analysis_mode == "high_accuracy"
    assert parsed.stem_separation_provider == "none"
    assert parsed.shifts == 2
    assert parsed.multi_filter is True

    parsed_engine = p.parse_args(
        [
            "import",
            "in.wav",
            "--out",
            "o.songpack",
            "--drum-engine",
            "mr_mt3_drums",
        ]
    )
    assert parsed_engine.drum_filter == "mr_mt3_drums"


def test_research_decision_defaults_are_quality_first_and_fail_safe() -> None:
    from aural_ingest import cli

    assert cli.DEFAULT_BEAT_ANALYSIS_MODE == "high_accuracy"
    assert cli.BEAT_TEMPO_PRODUCTION_POLICY["production_default"] == "librosa_first"
    assert cli.BEAT_TEMPO_PRODUCTION_POLICY["essentia_status"] == "research_candidate_not_default"
    assert cli.STEM_SEPARATION_PROVIDER_POLICY["demucs_support_status"] == "optional_experimental"
    assert (
        cli.STEM_SEPARATION_PROVIDER_POLICY["absence_behavior"]
        == "skip_separation_and_continue_with_mix_or_provided_stems"
    )
    assert cli.BENCHMARK_THRESHOLD_POLICY["mode"] == "warn"
    assert cli.BENCHMARK_THRESHOLD_POLICY["strict_pr_blocking"] is False


def test_cmd_refine_piano_returns_2_for_missing_audio(tmp_path: Path) -> None:
    from aural_ingest import cli
    from aural_ingest.piano_benchmark import write_melodic_notes_midi
    from aural_ingest.transcription import MelodicNote

    source = tmp_path / "source.mid"
    write_melodic_notes_midi(
        [MelodicNote(t_on=0.0, t_off=0.25, pitch=60, velocity=80, instrument="keys")],
        source,
    )

    args = type("Args", (), {})()
    args.audio = str(tmp_path / "missing.wav")
    args.source_midi = str(source)
    args.reference_midi = None
    args.method = ["source_midi"]
    args.out_root = str(tmp_path / "runs")
    args.label = "unit"
    args.tolerance_ms = 60.0
    args.offset_tolerance_ms = 120.0
    args.velocity_tolerance = 20
    args.source_offset_sec = 0.0
    args.reference_offset_sec = 0.0
    args.bpm = 120.0

    assert cli.cmd_refine_piano(args) == 2


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
    assert payload["algorithm_metadata"]["combined_filter"]["backend"] == "heuristic"
    assert payload["class_order"] == ["kick", "snare", "hi_hat", "crash", "ride", "tom1", "tom2", "tom3"]

    results = {result["algorithm"]: result for result in payload["results"]}
    assert results["combined_filter"]["per_class"]["snare"]["tp"] == 1
    assert results["adaptive_beat_grid"]["per_class"]["snare"]["fn"] == 1
    assert results["adaptive_beat_grid"]["confusions"] == [
        {"reference_class": "snare", "predicted_class": "tom1", "count": 1}
    ]


def test_cmd_runtime_check_emits_dependency_and_modelpack_snapshot(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from aural_ingest import cli

    basic_pitch_model = tmp_path / "basic_pitch" / "saved_models" / "icassp_2022" / "nmp.onnx"
    basic_pitch_model.parent.mkdir(parents=True, exist_ok=True)
    basic_pitch_model.write_bytes(b"basic-pitch")
    ffmpeg_path = tmp_path / "ffmpeg.exe"
    ffmpeg_path.write_bytes(b"ffmpeg")
    demucs_zip = tmp_path / "demucs_6.zip"
    demucs_zip.write_bytes(b"demucs")
    mt3_checkpoint = tmp_path / "mr_mt3" / "mt3.pth"
    mt3_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    mt3_checkpoint.write_bytes(b"mt3")

    fake_modules = {
        "librosa": "0.11.0",
        "numpy": "2.2.0",
        "torch": "2.11.0",
        "torchaudio": "2.11.0",
        "mt3_infer": "0.1.3",
        "demucs": "4.0.1",
    }
    for name, version in fake_modules.items():
        module = types.ModuleType(name)
        module.__version__ = version
        module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules["numpy"].float32 = "float32"
    sys.modules["numpy"].zeros = lambda size, dtype=None: [0.0] * size
    class _FakeMidi:
        tracks = []

    class FakeAdapter:
        def transcribe(self, audio, sr=16000):
            assert sr == 16000
            return _FakeMidi()

    sys.modules["mt3_infer"].load_model = lambda *args, **kwargs: FakeAdapter()
    monkeypatch.setattr(cli, "ensure_mt3_transformers_compat", lambda: None)
    monkeypatch.setattr(cli, "resolve_basic_pitch_model_path", lambda _roots: basic_pitch_model)
    monkeypatch.setattr(cli, "_default_basic_pitch_model_roots", lambda: [tmp_path])
    monkeypatch.setattr(cli, "_resolve_ffmpeg_path", lambda: str(ffmpeg_path))
    monkeypatch.setattr(
        cli,
        "_resolve_demucs_modelpack",
        lambda _config: (demucs_zip, {"id": "demucs_6", "version": "test"}, None),
    )

    monkeypatch.setattr(
        cli,
        "available_mt3_modelpacks",
        lambda: {
                "mr_mt3_drums": {
                    "ok": True,
                    "model_id": "mr_mt3",
                    "modelpack_id": "mr_mt3",
                    "modelpack_version": "0.0.1",
                    "checkpoint_path": str(mt3_checkpoint),
                    "modelpack_root": str(mt3_checkpoint.parent),
                },
            "yourmt3_drums": {"ok": False, "error": "missing modelpack"},
        },
    )

    rc = cli.cmd_runtime_check(type("Args", (), {})())
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["policies"]["beat_tempo"]["production_default"] == "librosa_first"
    assert payload["policies"]["stem_separation"]["demucs_support_status"] == "optional_experimental"
    assert payload["dependencies"]["librosa"]["required"] is False
    assert payload["dependencies"]["torch"]["version"] == "2.11.0"
    assert payload["drum_engines"]["mr_mt3_drums"]["ok"] is True
    assert payload["drum_engines"]["mr_mt3_drums"]["loadable"] is True
    assert payload["drum_engines"]["mr_mt3_drums"]["adapter_class"] == "FakeAdapter"
    assert payload["drum_engines"]["mr_mt3_drums"]["transcribe_smoke_ok"] is True
    assert payload["assets"]["basic_pitch_model"]["ok"] is True
    assert payload["assets"]["basic_pitch_model"]["sha256"]
    assert payload["assets"]["demucs_modelpack"]["manifest"]["id"] == "demucs_6"
    assert payload["assets"]["mt3_checkpoints"]["mr_mt3_drums"]["sha256"]
    assert payload["stages"]["separate_stems"]["enabled"] is True
    assert payload["stages"]["separate_stems"]["required_models"][0]["version"] == "test"
    assert payload["stages"]["transcribe_drums"]["variants"]["mr_mt3_drums"]["enabled"] is True
    assert payload["stages"]["transcribe_drums"]["variants"]["mr_mt3_drums"]["required_models"][0]["version"] == "0.0.1"
    assert payload["stages"]["transcribe_drums"]["variants"]["yourmt3_drums"]["enabled"] is False


def test_main_runs_stages_command(capsys) -> None:
    from aural_ingest import cli

    rc = cli.main(["stages"])
    assert rc == 0
    assert '"id"' in capsys.readouterr().out


def test_runtime_stage_snapshot_disables_model_variants_when_packs_missing() -> None:
    from aural_ingest import cli

    stages = cli._runtime_stage_snapshot(
        {
            "mr_mt3_drums": {
                "ok": False,
                "model_id": "mr_mt3",
                "modelpack_id": "mr_mt3",
                "error": "missing modelpack",
            },
            "yourmt3_drums": {
                "ok": False,
                "model_id": "yourmt3",
                "modelpack_id": "yourmt3",
                "error": "missing modelpack",
            },
        },
        None,
        None,
        "missing demucs pack",
    )

    assert stages["separate_stems"]["enabled"] is False
    assert stages["separate_stems"]["required_models"][0]["installed"] is False
    assert stages["transcribe_drums"]["variants"]["mr_mt3_drums"]["enabled"] is False
    assert stages["transcribe_drums"]["variants"]["mr_mt3_drums"]["required_models"][0]["installed"] is False


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


def test_cmd_import_dir_synthesizes_mix_from_configured_input_stems(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    drums = src_dir / "drums.wav"
    bass = src_dir / "bass.wav"
    drums.write_bytes(b"drums")
    bass.write_bytes(b"bass")

    seen = {}

    def fake_cmd_import(args):
        seen["input_audio_path"] = args.input_audio_path
        seen["config"] = args.config
        assert Path(args.input_audio_path).name == "mix.wav"
        assert Path(args.input_audio_path).is_file()
        return 0

    monkeypatch.setattr(cli, "cmd_import", fake_cmd_import)
    monkeypatch.setattr(
        cli,
        "_synthesize_mix_wav_from_input_stems",
        lambda dst, _config: (dst.write_bytes(b"mix"), (1.0, 48_000))[1],
    )

    args = type("Args", (), {})()
    args.input_dir_path = str(src_dir)
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = json.dumps(
        {
            "disable_stem_separation": True,
            "input_stem_paths": {
                "drums": str(drums),
                "bass": str(bass),
            },
        }
    )
    args.title = "t"
    args.artist = "a"
    args.duration_sec = None

    assert cli.cmd_import_dir(args) == 0
    assert Path(seen["input_audio_path"]).name == "mix.wav"
    assert seen["config"] == args.config


def test_cmd_import_unsupported_chart_format_returns_2_for_missing_unsupported_chart_format_file(tmp_path: Path) -> None:
    from aural_ingest import cli

    args = type("Args", (), {})()
    args.unsupported_chart_format_path = str(tmp_path / "nope.unsupported_chart_format")
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = None
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import_unsupported_chart_format(args) == 2


def test_find_audio_source_for_unsupported_chart_format_prefers_referenced_files(tmp_path: Path) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "song"
    src_dir.mkdir(parents=True, exist_ok=True)
    unsupported_chart_format = src_dir / "chart.unsupported_chart_format"
    (src_dir / "audio").mkdir(parents=True, exist_ok=True)
    ref_audio = src_dir / "audio" / "drums.wav"
    ref_audio.write_bytes(b"x")
    # fallback mix file should not be chosen when referenced audio exists
    (src_dir / "mix.wav").write_bytes(b"x")

    unsupported_chart_format.write_text("#WAV01 audio/drums.wav\n", encoding="utf-8")

    assert cli._find_audio_source_for_unsupported_chart_format(unsupported_chart_format) == ref_audio


def test_cmd_import_unsupported_chart_format_forwards_selected_audio_to_cmd_import(tmp_path: Path, monkeypatch) -> None:
    from aural_ingest import cli

    src_dir = tmp_path / "song"
    src_dir.mkdir(parents=True, exist_ok=True)
    unsupported_chart_format = src_dir / "chart.unsupported_chart_format"
    wav = src_dir / "mix.wav"
    wav.write_bytes(b"x")
    unsupported_chart_format.write_text("; minimal unsupported_chart_format\n", encoding="utf-8")

    seen = {}

    def fake_cmd_import(args):
        seen["input_audio_path"] = args.input_audio_path
        seen["title"] = args.title
        return 0

    monkeypatch.setattr(cli, "cmd_import", fake_cmd_import)

    args = type("Args", (), {})()
    args.unsupported_chart_format_path = str(unsupported_chart_format)
    args.out = str(tmp_path / "x.songpack")
    args.profile = "full"
    args.config = "{}"
    args.title = None
    args.artist = None
    args.duration_sec = None

    assert cli.cmd_import_unsupported_chart_format(args) == 0
    assert seen["input_audio_path"] == str(wav)
    # default title should use unsupported_chart_format stem when not provided
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
    args.beat_analysis_mode = "standard"
    args.stem_separation_provider = "auto"
    args.stem_separation_provider_path = None
    args.shifts = 1
    args.multi_filter = False

    # Unknown drum filter is accepted and normalized by recovery policy.
    tr_opts, tr_err = cli._resolve_transcription_options(args)
    assert tr_err is None
    assert tr_opts is not None
    assert tr_opts["drum_filter"] == "combined_filter"
    assert tr_opts["warnings"]

    args.drum_filter = "combined_filter"
    args.melodic_method = "not_valid"
    assert cli.cmd_import(args) == 2

    args.melodic_method = "auto"
    args.shifts = 0
    assert cli.cmd_import(args) == 2

    args.shifts = 1
    args.beat_analysis_mode = "broken"
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
        seen["drum_silence_gate_dbfs"] = args.drum_silence_gate_dbfs
        seen["drum_silence_gate_window_ms"] = args.drum_silence_gate_window_ms
        seen["drum_silence_gate_disabled"] = args.drum_silence_gate_disabled
        seen["melodic_method"] = args.melodic_method
        seen["beat_analysis_mode"] = args.beat_analysis_mode
        seen["stem_separation_provider"] = args.stem_separation_provider
        seen["stem_separation_provider_path"] = args.stem_separation_provider_path
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
    args.drum_silence_gate_dbfs = -42.0
    args.drum_silence_gate_window_ms = 45.0
    args.drum_silence_gate_disabled = True
    args.melodic_method = "basic_pitch"
    args.beat_analysis_mode = "high_accuracy"
    args.stem_separation_provider = "none"
    args.stem_separation_provider_path = None
    args.shifts = 3
    args.multi_filter = True

    assert cli.cmd_import_dir(args) == 0
    assert seen == {
        "drum_filter": "dsp_bandpass_improved",
        "drum_silence_gate_dbfs": -42.0,
        "drum_silence_gate_window_ms": 45.0,
        "drum_silence_gate_disabled": True,
        "melodic_method": "basic_pitch",
        "beat_analysis_mode": "high_accuracy",
        "stem_separation_provider": "none",
        "stem_separation_provider_path": None,
        "shifts": 3,
        "multi_filter": True,
    }
