//! AuralSong `manifest.json` parsing shared by both Tauri shells.
//!
//! The parser is intentionally permissive: it pulls out the five summary fields
//! the library panel renders and leaves everything else untyped. A wrong field
//! type degrades to `None` rather than an error (see the regression tests).

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Read;
use std::path::Path;

/// Best-effort summary of the five fields the library panel renders.
#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct ManifestSummary {
    pub schema_version: Option<String>,
    pub song_id: Option<String>,
    pub title: Option<String>,
    pub artist: Option<String>,
    pub duration_sec: Option<f64>,
}

/// One row of the songs-folder scan: a `.auralsong` container plus its parsed
/// summary (or an error explaining why it could not be read).
#[derive(Debug, Serialize)]
pub struct AuralSongScanEntry {
    pub container_path: String,
    pub kind: String,
    pub ok: bool,
    pub manifest: Option<ManifestSummary>,
    pub error: Option<String>,
}

/// Strip a leading UTF-8 BOM if present.
///
/// PowerShell's `Set-Content -Encoding UTF8` writes a BOM by default, and
/// serde_json rejects it ("expected value at line 1 column 1") -- which would
/// silently hide the auralsong from the Play Songs scan with no actionable
/// error.
fn strip_bom(raw: &str) -> &str {
    raw.strip_prefix('\u{feff}').unwrap_or(raw)
}

/// Parse `manifest.json` text into a [`ManifestSummary`].
pub fn parse_manifest_json(raw: &str) -> Result<ManifestSummary, String> {
    let raw = strip_bom(raw);

    let v: serde_json::Value =
        serde_json::from_str(raw).map_err(|e| format!("invalid JSON: {e}"))?;

    // Keep it flexible: pull out common fields if present.
    Ok(ManifestSummary {
        schema_version: v
            .get("schema_version")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        song_id: v
            .get("song_id")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        title: v
            .get("title")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        artist: v
            .get("artist")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        duration_sec: v.get("duration_sec").and_then(|x| x.as_f64()),
    })
}

/// Parse `manifest.json` text into the raw, unmodified JSON value (BOM stripped).
pub fn parse_manifest_raw(raw: &str) -> Result<serde_json::Value, String> {
    let raw = strip_bom(raw);
    serde_json::from_str(raw).map_err(|e| format!("invalid JSON: {e}"))
}

/// Read and summarize `manifest.json` from a directory-style `.auralsong`.
pub fn read_dir_manifest(auralsong_dir: &Path) -> Result<ManifestSummary, String> {
    let manifest_path = auralsong_dir.join("manifest.json");
    let raw = fs::read_to_string(&manifest_path)
        .map_err(|e| format!("read {}: {e}", manifest_path.display()))?;
    parse_manifest_json(&raw)
}

/// Read and summarize `manifest.json` from a zip-style `.auralsong`.
pub fn read_zip_manifest(auralsong_zip: &Path) -> Result<ManifestSummary, String> {
    let raw = read_zip_manifest_text(auralsong_zip)?;
    parse_manifest_json(&raw)
}

/// Read the raw JSON value of `manifest.json` from a directory-style `.auralsong`.
pub fn read_dir_manifest_raw(auralsong_dir: &Path) -> Result<serde_json::Value, String> {
    let manifest_path = auralsong_dir.join("manifest.json");
    let raw = fs::read_to_string(&manifest_path)
        .map_err(|e| format!("read {}: {e}", manifest_path.display()))?;
    parse_manifest_raw(&raw)
}

/// Read the raw JSON value of `manifest.json` from a zip-style `.auralsong`.
pub fn read_zip_manifest_raw(auralsong_zip: &Path) -> Result<serde_json::Value, String> {
    let raw = read_zip_manifest_text(auralsong_zip)?;
    parse_manifest_raw(&raw)
}

fn read_zip_manifest_text(auralsong_zip: &Path) -> Result<String, String> {
    let f = fs::File::open(auralsong_zip)
        .map_err(|e| format!("open {}: {e}", auralsong_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    let mut file = archive
        .by_name("manifest.json")
        .map_err(|e| format!("zip missing manifest.json: {e}"))?;
    let mut raw = String::new();
    file.read_to_string(&mut raw)
        .map_err(|e| format!("zip read manifest.json: {e}"))?;
    Ok(raw)
}

#[cfg(test)]
mod manifest_scan_tests {
    //! Regression tests for AuralSong discovery shared by both shells.
    //!
    //! Context: an AuralSong written by `aural_ingest import` did not surface
    //! in the Play Songs panel. The Rust scan reads `manifest.json` and
    //! extracts five fields (schema_version, song_id, title, artist,
    //! duration_sec); a silent type mismatch on any of these turns into
    //! `None` and the UI renders "(missing title)" with no error.
    //!
    //! These tests pin the parser's contract against the exact JSON shape
    //! `aural_ingest` produces (see python/ingest/src/aural_ingest/cli.py
    //! init_auralsong stage). The Python side has a matching test that
    //! validates its emitter against packages/auralsong/schemas/manifest.schema.json.

    use super::{
        parse_manifest_json, parse_manifest_raw, read_dir_manifest, read_dir_manifest_raw,
    };
    use std::fs;
    use tempfile::tempdir;

    /// JSON literal matching the shape aural_ingest writes to manifest.json
    /// after the init_auralsong stage (post-decode update fills in duration).
    fn aural_ingest_manifest_json() -> &'static str {
        r#"{
          "schema_version": "1.0.0",
          "song_id": "3333ca45d864ceb68a9e7e5bc4932546",
          "title": "Heaven Whispered",
          "artist": "Book of Psalms (Psalm 19)",
          "duration_sec": 288.44,
          "source": {
            "original_filename": "Psalm 19 - Heaven Whispered.wav",
            "original_sha256": "0d388fabf6169dca693adde70a816356841b0e2ed2b219a30d30fdda4965e8e1",
            "ingest_timestamp": "2026-05-21T20:15:21Z"
          },
          "timing": {
            "audio_sample_rate_hz": 44100,
            "audio_start_offset_sec": 0.0,
            "timebase": "audio"
          },
          "pipeline": {
            "pipeline_id": "aural_ingest",
            "pipeline_version": "0.1.0",
            "profile": "gameplay_default",
            "stage_fingerprints": {"init_auralsong": "0.1.0"},
            "transcription": {"drum_engine": "combined_filter"}
          },
          "recognition": {},
          "assets": {
            "audio": {"mix_path": "audio/mix.wav", "stems": {"drums": "audio/stems/drums.wav"}},
            "features": {"beats_path": "features/beats.json"},
            "midi": {"notes_path": "features/notes.mid"}
          }
        }"#
    }

    #[test]
    fn parse_aural_ingest_manifest_extracts_all_five_required_fields() {
        let parsed = parse_manifest_json(aural_ingest_manifest_json())
            .expect("aural_ingest manifest must parse");

        assert_eq!(parsed.schema_version.as_deref(), Some("1.0.0"));
        assert_eq!(
            parsed.song_id.as_deref(),
            Some("3333ca45d864ceb68a9e7e5bc4932546")
        );
        assert_eq!(parsed.title.as_deref(), Some("Heaven Whispered"));
        assert_eq!(parsed.artist.as_deref(), Some("Book of Psalms (Psalm 19)"));
        assert_eq!(parsed.duration_sec, Some(288.44));
    }

    #[test]
    fn parse_init_stage_manifest_with_zero_duration_and_empty_artist() {
        // Partially-imported auralsong: init_auralsong stage completed but the
        // import was killed before decode_audio finalized duration_sec.
        // These minimal field values must still survive parsing -- the game's
        // permissive scan otherwise drops the auralsong with no error message.
        let raw = r#"{
            "schema_version": "1.0.0",
            "song_id": "abc123",
            "title": "Untitled",
            "artist": "",
            "duration_sec": 0.0
        }"#;
        let parsed = parse_manifest_json(raw).expect("init-stage manifest must parse");
        assert_eq!(parsed.artist.as_deref(), Some(""));
        assert_eq!(parsed.duration_sec, Some(0.0));
    }

    #[test]
    fn parse_manifest_handles_missing_optional_fields() {
        // The parser is intentionally permissive; absent fields become None
        // rather than errors. Verify we don't accidentally tighten this and
        // start rejecting existing on-disk auralsongs.
        let parsed = parse_manifest_json("{}").expect("empty object must parse");
        assert!(parsed.schema_version.is_none());
        assert!(parsed.title.is_none());
        assert!(parsed.duration_sec.is_none());
    }

    #[test]
    fn parse_manifest_returns_none_for_wrong_field_types() {
        // Silent-failure regression: if the Python emitter ever changes
        // `duration_sec` from a number to a string (or `title` from a string
        // to a number), the parser falls back to None and the UI shows
        // "(missing title)". This test documents that behavior so anyone
        // tightening parse_manifest_json knows what they're locking in.
        let raw = r#"{
            "schema_version": 1,
            "title": 123,
            "duration_sec": "288.44"
        }"#;
        let parsed = parse_manifest_json(raw).expect("type mismatches still parse as JSON");
        assert!(
            parsed.schema_version.is_none(),
            "schema_version as int must not parse as string"
        );
        assert!(
            parsed.title.is_none(),
            "title as int must not parse as string"
        );
        assert!(
            parsed.duration_sec.is_none(),
            "duration_sec as string must not parse as f64"
        );
    }

    #[test]
    fn parse_manifest_raw_preserves_wrong_field_types() {
        // parse_manifest_raw returns the JSON verbatim (used for the details
        // pane), so type-confused fields survive as their original JSON types
        // rather than being coerced or dropped.
        let raw = r#"{ "schema_version": 1, "duration_sec": "288.44" }"#;
        let value = parse_manifest_raw(raw).expect("raw parse keeps any valid JSON");
        assert!(value.get("schema_version").unwrap().is_number());
        assert!(value.get("duration_sec").unwrap().is_string());
    }

    #[test]
    fn parse_manifest_rejects_invalid_json() {
        let result = parse_manifest_json("this is not json");
        assert!(result.is_err(), "non-JSON input must surface as error");
        let err = result.unwrap_err();
        assert!(
            err.contains("invalid JSON"),
            "error message must explain why: {err}"
        );
    }

    #[test]
    fn parse_manifest_raw_rejects_invalid_json() {
        let result = parse_manifest_raw("{ not valid");
        assert!(result.is_err(), "non-JSON input must surface as error");
        assert!(result.unwrap_err().contains("invalid JSON"));
    }

    #[test]
    fn parse_manifest_json_strips_utf8_bom() {
        // serde_json rejects a leading BOM; the parser must strip it so a
        // BOM-prefixed manifest still yields the summary fields.
        let mut bom = "\u{feff}".to_string();
        bom.push_str(aural_ingest_manifest_json());
        let parsed = parse_manifest_json(&bom).expect("BOM-prefixed manifest must parse");
        assert_eq!(parsed.title.as_deref(), Some("Heaven Whispered"));
    }

    #[test]
    fn parse_manifest_raw_strips_utf8_bom() {
        let bom = format!("\u{feff}{}", r#"{ "schema_version": "1.0.0" }"#);
        let value = parse_manifest_raw(&bom).expect("BOM-prefixed raw manifest must parse");
        assert_eq!(value.get("schema_version").unwrap().as_str(), Some("1.0.0"));
    }

    #[test]
    fn read_dir_manifest_finds_and_parses_aural_ingest_manifest_on_disk() {
        // End-to-end: create a tempdir mirroring `<portable>/data/songs/<x>.auralsong/`
        // with a real manifest.json and assert the scanner returns the same
        // fields as the in-memory parse. Catches IO-layer regressions
        // (encoding, BOM, line endings, missing read perms).
        let tmp = tempdir().expect("tempdir");
        let auralsong_dir = tmp.path().join("test.auralsong");
        fs::create_dir(&auralsong_dir).expect("mkdir");
        fs::write(
            auralsong_dir.join("manifest.json"),
            aural_ingest_manifest_json(),
        )
        .expect("write manifest.json");

        let summary = read_dir_manifest(&auralsong_dir).expect("scan should succeed");
        assert_eq!(summary.title.as_deref(), Some("Heaven Whispered"));
        assert_eq!(summary.duration_sec, Some(288.44));
    }

    #[test]
    fn read_dir_manifest_raw_finds_manifest_on_disk() {
        let tmp = tempdir().expect("tempdir");
        let auralsong_dir = tmp.path().join("test.auralsong");
        fs::create_dir(&auralsong_dir).expect("mkdir");
        fs::write(
            auralsong_dir.join("manifest.json"),
            aural_ingest_manifest_json(),
        )
        .expect("write manifest.json");

        let value = read_dir_manifest_raw(&auralsong_dir).expect("raw scan should succeed");
        assert_eq!(value.get("title").unwrap().as_str(), Some("Heaven Whispered"));
    }

    #[test]
    fn read_dir_manifest_errors_clearly_when_manifest_missing() {
        let tmp = tempdir().expect("tempdir");
        let auralsong_dir = tmp.path().join("broken.auralsong");
        fs::create_dir(&auralsong_dir).expect("mkdir");
        // No manifest.json written.
        let err =
            read_dir_manifest(&auralsong_dir).expect_err("missing manifest must be an error");
        assert!(err.contains("manifest.json"), "error must name the file: {err}");
    }

    #[test]
    fn read_dir_manifest_tolerates_utf8_bom() {
        // PowerShell's `Set-Content -Encoding UTF8` writes a BOM by default,
        // and the portable-manifest writer in create_portable.ps1 uses that.
        // If aural_ingest ever switches to a Windows-side writer with the
        // same default, manifest.json would acquire a BOM.
        let tmp = tempdir().expect("tempdir");
        let auralsong_dir = tmp.path().join("bom.auralsong");
        fs::create_dir(&auralsong_dir).expect("mkdir");
        let mut bytes = b"\xef\xbb\xbf".to_vec();
        bytes.extend_from_slice(aural_ingest_manifest_json().as_bytes());
        fs::write(auralsong_dir.join("manifest.json"), bytes).expect("write");

        let summary =
            read_dir_manifest(&auralsong_dir).expect("manifest.json with UTF-8 BOM must parse");
        assert_eq!(summary.title.as_deref(), Some("Heaven Whispered"));
    }
}
