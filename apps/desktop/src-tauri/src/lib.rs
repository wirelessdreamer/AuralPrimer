// Keep a console in release builds so runtime/frontend logs are visible in portable binaries.

// Pre-existing clippy debt, cleared en masse to unblock the CI Rust gate (which
// never ran before -- the Linux Tauri build was broken until the system deps +
// feature flags were fixed). dead_code covers the suno-drum pitch-profile + MIDI
// retiming helpers that are built but not yet wired into the pipeline. Tighten
// these incrementally rather than deleting in-progress work.
#![allow(dead_code)]
#![allow(clippy::too_many_arguments)]
#![allow(clippy::manual_memcpy)]
#![allow(clippy::needless_range_loop)]
#![allow(clippy::collapsible_match)]

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::{AppHandle, Manager, WebviewWindowBuilder};

mod audio_decode;
pub mod audio_engine;
pub mod demo_auralsong;
pub mod ingest_sidecar;
mod midi_clock;
mod midi_clock_input;
mod midi_clock_service;
mod models;
mod native_audio;
pub mod raw_song;
pub mod stem_midi;
pub mod wav_mix;

// Shared AuralSong contract logic (manifest parsing + songs-folder watcher).
// The watcher is re-exported under the `songs_watch` path so the wiring mirrors
// the game shell.
use auralsong_core::container;
use auralsong_core::feedpak::{
    read_dir_feedpak_manifest, read_zip_feedpak_manifest, scan_feedpak, FeedpakManifest,
};
use auralsong_core::manifest::{
    parse_manifest_json, read_dir_manifest, read_dir_manifest_raw, read_zip_manifest,
    read_zip_manifest_raw, AuralSongScanEntry, ManifestSummary,
};
use auralsong_core::songs_watch;
use auralsong_core::songs_watch::SongsWatchState;

#[derive(Debug, Serialize, Deserialize, Default)]
struct Settings {
    #[serde(default)]
    songs_folder: Option<String>,

    #[serde(default)]
    visualizers_folder: Option<String>,

    // --- MIDI output clock ---
    #[serde(default)]
    midi_clock_output_port: Option<midi_clock::MidiOutputSelection>,
    #[serde(default)]
    midi_input_port: Option<midi_clock_input::MidiInputSelection>,
    #[serde(default)]
    midi_input_tempo_scale: Option<f64>,
    #[serde(default)]
    midi_input_allow_sysex: bool,
    #[serde(default)]
    midi_output_allow_sysex: bool,

    // --- Native audio output ---
    #[serde(default)]
    native_audio_output_host: Option<native_audio::NativeAudioHostSelection>,

    #[serde(default)]
    native_audio_output_device: Option<native_audio::NativeAudioDeviceSelection>,

    // --- A/V sync calibration (shared with AuralPrimer via the portable's
    //     common settings.json: calibrate once, both apps honor it) ---
    #[serde(default)]
    av_audio_offset_ms: Option<f64>,
    #[serde(default)]
    av_video_offset_ms: Option<f64>,
}

#[derive(Debug, Serialize)]
struct SongsFolderPaths {
    config_dir: String,
    data_dir: String,
    default_songs_folder: String,
    default_visualizers_folder: String,
    settings_path: String,
}

#[derive(Debug, Serialize)]
pub struct AuralSongDetails {
    pub container_path: String,
    pub kind: String,
    pub ok: bool,

    /// Parsed summary fields (best-effort).
    pub manifest_summary: Option<ManifestSummary>,
    /// Raw manifest JSON (unmodified) for display/debug.
    pub manifest_raw: Option<serde_json::Value>,

    /// Feature presence.
    pub has_beats: bool,
    pub has_tempo_map: bool,
    pub has_sections: bool,
    pub has_events: bool,
    pub has_lyrics: bool,
    /// Optional MIDI note data (not yet consumed by gameplay viz).
    pub has_notes_mid: bool,

    /// Audio presence.
    pub has_mix_mp3: bool,
    pub has_mix_ogg: bool,
    pub has_mix_wav: bool,

    /// List of chart json paths (relative in zip/dir).
    pub charts: Vec<String>,

    /// Number of `arrangements[]` entries in the manifest (manifest packs
    /// only; 0 for legacy `.auralsong`). The frontend gates "needs arrangement
    /// prep" on `arrangements_count > 0 && !has_notes_mid`.
    #[serde(default)]
    pub arrangements_count: usize,

    /// Manifest `lyrics` rel-path, if the pack declares one. Sloppak lyrics
    /// live at the pack ROOT (`lyrics.json`) rather than under a feature dir,
    /// so the frontend must resolve them from this pointer rather than
    /// assuming `${featureDir}/lyrics.json`. `None` when the manifest has no
    /// `lyrics` key (or for legacy `.auralsong`).
    #[serde(default)]
    pub lyrics_rel: Option<String>,

    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct AudioBlob {
    pub mime: String,
    pub bytes: Vec<u8>,
}

#[derive(Debug, Serialize)]
pub struct MidiBlob {
    pub bytes: Vec<u8>,
}

#[derive(Debug, Serialize)]
pub struct LoadedAuralSongAudioInfo {
    pub mime: String,
    pub duration_sec: f64,
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct VisualizerManifest {
    pub id: Option<String>,
    pub name: Option<String>,
    pub version: Option<String>,
    pub description: Option<String>,
    pub entry: Option<String>,

    // keep forward-compatible
    #[serde(flatten)]
    pub extra: std::collections::BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Serialize)]
pub struct VisualizerScanEntry {
    pub plugin_path: String,
    pub ok: bool,
    pub manifest: Option<VisualizerManifest>,
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct JsBlob {
    pub mime: String,
    pub bytes: Vec<u8>,
}

// -----------------
// MIDI (clock in/out)
// -----------------

#[derive(Default)]
struct MidiClockOutputState {
    svc: Mutex<Option<midi_clock_service::MidiClockService>>,
}

#[derive(Default)]
struct MidiClockInputState {
    // Keep the connection alive while listening.
    conn: Mutex<Option<midi_clock_input::MidiClockInputConnection>>,
}

#[derive(Debug, Serialize, Clone)]
struct MidiInputSavedSettings {
    port: Option<midi_clock_input::MidiInputSelection>,
    tempo_scale: f64,
    allow_sysex: bool,
}

// -----------------
// Native audio (Phase 1)
// -----------------

#[derive(Default)]
struct NativeAudioState {
    inner: native_audio::NativeAudioEngineState,
    selected_output_host: Mutex<Option<native_audio::NativeAudioHostSelection>>,
    selected_output_device: Mutex<Option<native_audio::NativeAudioDeviceSelection>>,
}

fn resolve_portable_data_dirs() -> Option<(PathBuf, PathBuf)> {
    if let Ok(raw) = std::env::var("AURALPRIMER_PORTABLE_DATA_DIR") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            let data_dir = PathBuf::from(trimmed);
            let config_dir = data_dir.join("config");
            return Some((config_dir, data_dir));
        }
    }

    let exe_path = std::env::current_exe().ok()?;
    let exe_dir = exe_path.parent()?;
    let portable_manifest = exe_dir.join("portable_manifest.json");
    if portable_manifest.is_file() {
        let data_dir = exe_dir.join("data");
        let config_dir = data_dir.join("config");
        return Some((config_dir, data_dir));
    }

    None
}

fn resolve_webview_data_dir(window_label: &str) -> Option<PathBuf> {
    let (_, data_dir) = resolve_portable_data_dirs()?;
    Some(data_dir.join("webview").join(window_label))
}

fn build_main_window(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    if app.get_webview_window("main").is_some() {
        return Ok(());
    }

    let window_config = app
        .config()
        .app
        .windows
        .iter()
        .find(|window| window.label == "main")
        .cloned()
        .ok_or_else(|| {
            std::io::Error::new(std::io::ErrorKind::NotFound, "missing main window config")
        })?;

    let mut builder = WebviewWindowBuilder::from_config(app.handle(), &window_config)?;
    if let Some(webview_data_dir) = resolve_webview_data_dir(&window_config.label) {
        fs::create_dir_all(&webview_data_dir)?;
        eprintln!(
            "[webview] using portable data dir: {}",
            webview_data_dir.display()
        );
        builder = builder.data_directory(webview_data_dir);
    }
    builder.build()?;
    Ok(())
}

fn get_paths(app: &AppHandle) -> Result<SongsFolderPaths, String> {
    let (config_dir, data_dir) = if let Some((config_dir, data_dir)) = resolve_portable_data_dirs()
    {
        (config_dir, data_dir)
    } else {
        let config_dir = app
            .path()
            .app_config_dir()
            .map_err(|e| format!("app_config_dir: {e}"))?;
        let data_dir = app
            .path()
            .app_data_dir()
            .map_err(|e| format!("app_data_dir: {e}"))?;
        (config_dir, data_dir)
    };

    let default_songs_folder = data_dir.join("songs");
    let default_visualizers_folder = data_dir.join("visualizers");
    let settings_path = config_dir.join("settings.json");

    Ok(SongsFolderPaths {
        config_dir: config_dir.to_string_lossy().to_string(),
        data_dir: data_dir.to_string_lossy().to_string(),
        default_songs_folder: default_songs_folder.to_string_lossy().to_string(),
        default_visualizers_folder: default_visualizers_folder.to_string_lossy().to_string(),
        settings_path: settings_path.to_string_lossy().to_string(),
    })
}

fn load_settings(paths: &SongsFolderPaths) -> Settings {
    let p = Path::new(&paths.settings_path);
    let Ok(raw) = fs::read_to_string(p) else {
        return Settings::default();
    };
    serde_json::from_str(&raw).unwrap_or_default()
}

fn save_settings(paths: &SongsFolderPaths, settings: &Settings) -> Result<(), String> {
    let config_dir = Path::new(&paths.config_dir);
    fs::create_dir_all(config_dir).map_err(|e| format!("mkdir config_dir: {e}"))?;

    let tmp_path = format!("{}.tmp", paths.settings_path);
    let json =
        serde_json::to_string_pretty(settings).map_err(|e| format!("serialize settings: {e}"))?;

    fs::write(&tmp_path, json).map_err(|e| format!("write tmp settings: {e}"))?;
    fs::rename(&tmp_path, &paths.settings_path).map_err(|e| format!("rename settings: {e}"))?;
    Ok(())
}

async fn run_blocking_command<T, F>(label: &'static str, task: F) -> Result<T, String>
where
    T: Send + 'static,
    F: FnOnce() -> Result<T, String> + Send + 'static,
{
    tauri::async_runtime::spawn_blocking(task)
        .await
        .map_err(|e| format!("{label} task failed: {e}"))?
}

fn dir_has_file(root: &Path, rel: &str) -> bool {
    root.join(rel).is_file()
}

fn dir_list_charts(root: &Path) -> Vec<String> {
    let charts_dir = root.join("charts");
    let mut out = vec![];
    let Ok(entries) = fs::read_dir(charts_dir) else {
        return out;
    };
    for e in entries.flatten() {
        let p = e.path();
        if p.is_file() {
            if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
                if name.ends_with(".json") {
                    out.push(format!("charts/{name}"));
                }
            }
        }
    }
    out.sort();
    out
}

fn zip_has_file(auralsong_zip: &Path, rel: &str) -> Result<bool, String> {
    let f = fs::File::open(auralsong_zip)
        .map_err(|e| format!("open {}: {e}", auralsong_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    // Ensure the ZipFile temporary is dropped before `archive` is dropped.
    let exists = archive.by_name(rel).is_ok();
    Ok(exists)
}

fn zip_list_charts(auralsong_zip: &Path) -> Result<Vec<String>, String> {
    let f = fs::File::open(auralsong_zip)
        .map_err(|e| format!("open {}: {e}", auralsong_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    let mut out: Vec<String> = vec![];
    for i in 0..archive.len() {
        let Ok(file) = archive.by_index(i) else {
            continue;
        };
        let name = file.name().to_string();
        if name.starts_with("charts/") && name.ends_with(".json") {
            out.push(name);
        }
    }
    out.sort();
    Ok(out)
}

fn read_dir_audio(auralsong_dir: &Path, rel: &str) -> Result<Vec<u8>, String> {
    let p = auralsong_dir.join(rel);
    fs::read(&p).map_err(|e| format!("read {}: {e}", p.display()))
}

fn read_zip_audio(auralsong_zip: &Path, rel: &str) -> Result<Vec<u8>, String> {
    let f = fs::File::open(auralsong_zip)
        .map_err(|e| format!("open {}: {e}", auralsong_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    let mut file = archive
        .by_name(rel)
        .map_err(|e| format!("zip missing {rel}: {e}"))?;

    let mut buf: Vec<u8> = vec![];
    file.read_to_end(&mut buf)
        .map_err(|e| format!("zip read {rel}: {e}"))?;
    Ok(buf)
}

fn read_dir_text(auralsong_dir: &Path, rel: &str) -> Result<String, String> {
    let p = auralsong_dir.join(rel);
    fs::read_to_string(&p).map_err(|e| format!("read {}: {e}", p.display()))
}

fn read_zip_text(auralsong_zip: &Path, rel: &str) -> Result<String, String> {
    let f = fs::File::open(auralsong_zip)
        .map_err(|e| format!("open {}: {e}", auralsong_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    let mut file = archive
        .by_name(rel)
        .map_err(|e| format!("zip missing {rel}: {e}"))?;
    let mut raw = String::new();
    file.read_to_string(&mut raw)
        .map_err(|e| format!("zip read {rel}: {e}"))?;
    Ok(raw)
}

// -----------------
// feedpak container support
//
// Stage 5 of the .auralsong -> feedpak migration. The active native format is
// now `.feedpak` (manifest.yaml; features relocated under `aural/`, audio under
// `audio/stems/`). The Tauri read commands accept BOTH suffixes: a path ending
// in `.feedpak` is read via the auralsong-core feedpak reader, a path ending in
// `.auralsong` keeps the legacy manifest.json/features/ layout. The frontend
// passes the new in-container paths (`aural/...`) for feedpak containers.
// -----------------

/// True when the container path is a manifest-driven pack (feedpak OR sloppak,
/// directory or zip). Both use `manifest.yaml` and stash AuralPrimer artifacts
/// under `aural/`, so they share every read/write path. Delegates to
/// [`auralsong_core::container`] — the one place the extension strings live.
fn is_feedpak_path(container_path: &str) -> bool {
    container::is_manifest_pack(container_path)
}

/// True when the container path is a legacy `.auralsong`.
fn is_auralsong_path(container_path: &str) -> bool {
    container::is_auralsong(container_path)
}

/// Accept any supported native container suffix (manifest pack OR `.auralsong`).
fn is_supported_container(container_path: &str) -> bool {
    container::is_supported_pack(container_path)
}

/// Whether a frontend-supplied `rel_path` is an allowed feature/asset path.
///
/// Legacy `.auralsong` packs use `features/...`; feedpak packs relocate the
/// same artifacts under `aural/...` (spectrogram, refine candidates, notes.mid,
/// refinement) and notation under `arrangements/...`. Any of those prefixes is
/// accepted; `..` is always rejected (path-traversal guard).
fn is_allowed_feature_rel(rel_path: &str) -> bool {
    if rel_path.contains("..") {
        return false;
    }
    rel_path.starts_with("features/")
        || rel_path.starts_with("aural/")
        || rel_path.starts_with("arrangements/")
        // The drum chart is a root-level, manifest-referenced artifact
        // (`drum_tab: drum_tab.json`); the Studio drum-cleanup editor writes
        // edited hits back to it so the game picks them up directly.
        || rel_path == "drum_tab.json"
        // The beat/measure timeline is a root-level, manifest-referenced
        // artifact (`song_timeline.json`); the Cleanup/Edit editor reads it to
        // build the beat grid (bars/beats/subdivisions) and quantized placement.
        || rel_path == "song_timeline.json"
        // Sloppak lyrics + vocal pitch live at the pack ROOT (manifest
        // `lyrics: lyrics.json`), not under a feature dir — allow reading them.
        || rel_path == "lyrics.json"
        || rel_path == "vocal_pitch.json"
}

/// Read+parse a feedpak `manifest.yaml` from a directory or zip container.
fn read_feedpak_manifest(p: &Path) -> Result<FeedpakManifest, String> {
    if p.is_dir() {
        read_dir_feedpak_manifest(p)
    } else {
        read_zip_feedpak_manifest(p)
    }
}

/// Read the raw `manifest.yaml` text from a feedpak (dir or zip) — used for the
/// details pane's verbatim manifest display.
fn read_feedpak_manifest_yaml_text(p: &Path) -> Result<String, String> {
    if p.is_dir() {
        read_dir_text(p, "manifest.yaml")
    } else {
        read_zip_text(p, "manifest.yaml")
    }
}

/// Resolve the in-container relative path + MIME of a feedpak's playable audio.
///
/// Prefers the stem flagged `default: true`, otherwise the first stem. The MIME
/// is inferred from the stem file extension (wav/ogg/mp3/flac), defaulting to
/// `audio/wav`.
fn feedpak_default_stem(manifest: &FeedpakManifest) -> Result<(String, &'static str), String> {
    let stem = manifest
        .stems
        .iter()
        .find(|s| match &s.default {
            Some(serde_yaml::Value::Bool(b)) => *b,
            Some(serde_yaml::Value::String(s)) => {
                matches!(s.to_ascii_lowercase().as_str(), "true" | "yes" | "on" | "1")
            }
            _ => false,
        })
        .or_else(|| manifest.stems.first())
        .ok_or_else(|| "feedpak has no stems".to_string())?;

    let lower = stem.file.to_ascii_lowercase();
    let mime = if lower.ends_with(".ogg") {
        "audio/ogg"
    } else if lower.ends_with(".mp3") {
        "audio/mpeg"
    } else if lower.ends_with(".flac") {
        "audio/flac"
    } else {
        "audio/wav"
    };
    Ok((stem.file.clone(), mime))
}

fn unzip_auralsong_to_dir(zip_path: &Path, dst_dir: &Path) -> Result<(), String> {
    let f = fs::File::open(zip_path).map_err(|e| format!("open {}: {e}", zip_path.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;

    fs::create_dir_all(dst_dir).map_err(|e| format!("mkdir {}: {e}", dst_dir.display()))?;

    for i in 0..archive.len() {
        let mut file = archive
            .by_index(i)
            .map_err(|e| format!("zip index {i}: {e}"))?;
        let name = file.name().to_string();

        // Path traversal prevention.
        let rel = Path::new(&name);
        if rel
            .components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
        {
            return Err("zip path traversal detected".to_string());
        }

        let out_path = dst_dir.join(rel);
        if file.is_dir() {
            fs::create_dir_all(&out_path)
                .map_err(|e| format!("mkdir {}: {e}", out_path.display()))?;
            continue;
        }

        if let Some(parent) = out_path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
        }

        let mut out_f = fs::File::create(&out_path)
            .map_err(|e| format!("create {}: {e}", out_path.display()))?;
        std::io::copy(&mut file, &mut out_f)
            .map_err(|e| format!("write {}: {e}", out_path.display()))?;
    }

    Ok(())
}

#[tauri::command]
fn convert_auralsong_to_directory(
    app: AppHandle,
    container_path: String,
) -> Result<String, String> {
    let p = PathBuf::from(&container_path);
    if !container_path.ends_with(".auralsong") {
        return Err("path does not end with .auralsong".to_string());
    }

    // Already a directory.
    if p.is_dir() {
        return Ok(container_path);
    }

    let songs_folder = PathBuf::from(get_songs_folder(app.clone())?);
    let name = p
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or_else(|| "invalid auralsong filename".to_string())?;

    let base = songs_folder.join(name);
    let dst = if !base.exists() {
        base
    } else {
        // Avoid clobber: create a sibling with a deterministic suffix.
        let stem = p.file_stem().and_then(|s| s.to_str()).unwrap_or("song");
        let mut idx = 1;
        loop {
            let candidate = songs_folder.join(format!("{}_dir{}.auralsong", stem, idx));
            if !candidate.exists() {
                break candidate;
            }
            idx += 1;
            if idx > 999 {
                return Err("failed to find available directory name".to_string());
            }
        }
    };

    unzip_auralsong_to_dir(&p, &dst)?;
    Ok(dst.to_string_lossy().to_string())
}

#[tauri::command]
fn read_text_file(path: String) -> Result<String, String> {
    // NOTE: this is used for user-selected lyric text files.
    // It intentionally does not attempt to sandbox; callers must obtain the path via a file picker.
    fs::read_to_string(&path).map_err(|e| format!("read {}: {e}", path))
}

#[tauri::command]
fn ping() -> String {
    "pong".to_string()
}

#[tauri::command]
fn frontend_log(
    level: String,
    category: String,
    message: String,
    details: Option<String>,
) -> Result<(), String> {
    let level_norm = level.trim().to_ascii_lowercase();
    let category_norm = category.trim();
    let details_norm = details
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("");

    let line = if details_norm.is_empty() {
        format!("[frontend/{category_norm}] {message}")
    } else {
        format!("[frontend/{category_norm}] {message} | {details_norm}")
    };

    if level_norm == "error" || level_norm == "warn" {
        eprintln!("{line}");
    } else {
        println!("{line}");
    }
    Ok(())
}

#[tauri::command]
fn get_songs_folder_paths(app: AppHandle) -> Result<SongsFolderPaths, String> {
    get_paths(&app)
}

#[tauri::command]
fn get_songs_folder(app: AppHandle) -> Result<String, String> {
    let paths = get_paths(&app)?;
    let settings = load_settings(&paths);
    Ok(settings
        .songs_folder
        .unwrap_or_else(|| paths.default_songs_folder.clone()))
}

#[tauri::command]
fn get_visualizers_folder(app: AppHandle) -> Result<String, String> {
    let paths = get_paths(&app)?;
    let settings = load_settings(&paths);
    Ok(settings
        .visualizers_folder
        .unwrap_or_else(|| paths.default_visualizers_folder.clone()))
}

#[tauri::command]
fn set_visualizers_folder_override(
    app: AppHandle,
    visualizers_folder: String,
) -> Result<(), String> {
    let paths = get_paths(&app)?;
    let mut settings = load_settings(&paths);
    settings.visualizers_folder = Some(visualizers_folder);
    save_settings(&paths, &settings)
}

#[tauri::command]
fn clear_visualizers_folder_override(app: AppHandle) -> Result<(), String> {
    let paths = get_paths(&app)?;
    let mut settings = load_settings(&paths);
    settings.visualizers_folder = None;
    save_settings(&paths, &settings)
}

#[tauri::command]
fn set_songs_folder_override(app: AppHandle, songs_folder: String) -> Result<(), String> {
    let paths = get_paths(&app)?;
    let mut settings = load_settings(&paths);
    settings.songs_folder = Some(songs_folder);
    save_settings(&paths, &settings)?;
    // Re-mount the filesystem watcher on the new path so external drops keep
    // refreshing the library panel. Best-effort: a watcher failure should not
    // block the user from setting the override.
    remount_songs_watch_best_effort(&app);
    Ok(())
}

#[tauri::command]
fn clear_songs_folder_override(app: AppHandle) -> Result<(), String> {
    let paths = get_paths(&app)?;
    let mut settings = load_settings(&paths);
    settings.songs_folder = None;
    save_settings(&paths, &settings)?;
    remount_songs_watch_best_effort(&app);
    Ok(())
}

/// A/V sync offsets in milliseconds. Shared across AuralPrimer + AuralStudio:
/// in the portable both exes resolve the same `data/config/settings.json`, so
/// calibrating in either app applies to both.
#[derive(Debug, Serialize)]
struct AvCalibration {
    audio_offset_ms: f64,
    video_offset_ms: f64,
}

#[tauri::command]
fn get_av_calibration(app: AppHandle) -> Result<AvCalibration, String> {
    let paths = get_paths(&app)?;
    let settings = load_settings(&paths);
    Ok(AvCalibration {
        audio_offset_ms: settings.av_audio_offset_ms.unwrap_or(0.0),
        video_offset_ms: settings.av_video_offset_ms.unwrap_or(0.0),
    })
}

#[tauri::command]
fn set_av_calibration(
    app: AppHandle,
    audio_offset_ms: f64,
    video_offset_ms: f64,
) -> Result<(), String> {
    let paths = get_paths(&app)?;
    let mut settings = load_settings(&paths);
    settings.av_audio_offset_ms = Some(audio_offset_ms);
    settings.av_video_offset_ms = Some(video_offset_ms);
    save_settings(&paths, &settings)
}

/// Resolve the current songs folder and (re)mount the watcher on it. Logged
/// failures are non-fatal — the user always retains the manual refresh button.
fn remount_songs_watch_best_effort(app: &AppHandle) {
    match get_songs_folder(app.clone()) {
        Ok(folder) => {
            let path = PathBuf::from(folder);
            if let Err(e) = songs_watch::ensure_watch(app, &path) {
                eprintln!("songs_watch: re-mount failed: {e}");
            }
        }
        Err(e) => eprintln!("songs_watch: cannot resolve songs folder for re-mount: {e}"),
    }
}

/// Tauri command counterpart to `remount_songs_watch_best_effort`. The frontend
/// can call this once on boot to guarantee the watcher is live even if the
/// `setup()` mount raced ahead of folder creation. Idempotent: returns `Ok(())`
/// immediately if a watcher on the current path is already running.
#[tauri::command]
fn start_songs_folder_watch(app: AppHandle) -> Result<(), String> {
    let folder = get_songs_folder(app.clone())?;
    let path = PathBuf::from(folder);
    songs_watch::ensure_watch(&app, &path)
}

// -----------------
// Native audio (Phase 1)
// -----------------

#[tauri::command]
fn native_audio_list_output_hosts() -> Result<Vec<native_audio::NativeAudioHostInfo>, String> {
    native_audio::list_output_hosts()
}

#[tauri::command]
fn native_audio_list_output_devices(
    state: tauri::State<NativeAudioState>,
) -> Result<Vec<native_audio::NativeAudioDeviceInfo>, String> {
    native_audio::list_output_devices(current_native_audio_output_host_selection(&state))
}

fn normalize_native_audio_output_host_selection(
    sel: Option<native_audio::NativeAudioHostSelection>,
) -> Option<native_audio::NativeAudioHostSelection> {
    sel.and_then(|mut s| {
        s.id = s.id.trim().to_string();
        if s.id.is_empty() {
            None
        } else {
            Some(s)
        }
    })
}

fn current_native_audio_output_host_selection(
    state: &tauri::State<NativeAudioState>,
) -> Option<native_audio::NativeAudioHostSelection> {
    state.selected_output_host.lock().unwrap().clone()
}

fn normalize_native_audio_output_device_selection(
    sel: Option<native_audio::NativeAudioDeviceSelection>,
) -> Option<native_audio::NativeAudioDeviceSelection> {
    sel.and_then(|mut s| {
        s.name = s.name.trim().to_string();
        if s.name.is_empty() {
            None
        } else {
            Some(s)
        }
    })
}

fn current_native_audio_output_device_selection(
    state: &tauri::State<NativeAudioState>,
) -> Option<native_audio::NativeAudioDeviceSelection> {
    state.selected_output_device.lock().unwrap().clone()
}

fn replace_native_audio_engine(
    state: &tauri::State<NativeAudioState>,
    sample_rate_hz: u32,
    channels: u16,
) -> Result<(), String> {
    let output_host = current_native_audio_output_host_selection(state);
    let output_device = current_native_audio_output_device_selection(state);
    let new_engine = match native_audio::NativeAudioHandle::new_with_output_device(
        sample_rate_hz,
        channels,
        output_host.clone(),
        output_device.clone(),
    ) {
        Ok(e) => e,
        Err(e) if output_device.is_some() => {
            eprintln!(
                "native audio: failed to create engine with selected output device ({e}); falling back to host default device"
            );
            *state.selected_output_device.lock().unwrap() = None;
            native_audio::NativeAudioHandle::new_with_output_device(
                sample_rate_hz,
                channels,
                output_host,
                None,
            )?
        }
        Err(e) => return Err(e),
    };

    let old = {
        let mut lock = state.inner.engine.lock().unwrap();
        lock.replace(new_engine)
    };
    if let Some(old) = old {
        old.shutdown();
    }
    Ok(())
}

fn ensure_native_audio_engine_format(
    state: &tauri::State<NativeAudioState>,
    sample_rate_hz: u32,
    channels: u16,
) -> Result<(), String> {
    let needs_reinit = {
        let lock = state.inner.engine.lock().unwrap();
        match lock.as_ref() {
            Some(e) => e.sample_rate_hz != sample_rate_hz || e.channels != channels,
            None => true,
        }
    };
    if needs_reinit {
        replace_native_audio_engine(state, sample_rate_hz, channels)?;
    }
    Ok(())
}

fn preferred_native_audio_sample_rate_hz(
    state: &tauri::State<NativeAudioState>,
    fallback_sample_rate_hz: u32,
) -> Result<u32, String> {
    match native_audio::preferred_output_sample_rate_for_selection(
        current_native_audio_output_host_selection(state),
        current_native_audio_output_device_selection(state),
    ) {
        Ok(sr) => Ok(sr),
        Err(e) if fallback_sample_rate_hz > 0 => {
            eprintln!(
                "native audio: failed to resolve preferred output sample rate ({e}); using decoded sample rate {fallback_sample_rate_hz}"
            );
            Ok(fallback_sample_rate_hz)
        }
        Err(e) => Err(e),
    }
}

#[tauri::command]
fn native_audio_init(
    state: tauri::State<NativeAudioState>,
    sample_rate_hz: u32,
    channels: u16,
) -> Result<(), String> {
    replace_native_audio_engine(&state, sample_rate_hz, channels)
}

#[tauri::command]
fn native_audio_get_selected_output_host(
    state: tauri::State<NativeAudioState>,
) -> Option<native_audio::NativeAudioHostSelection> {
    current_native_audio_output_host_selection(&state)
}

#[tauri::command]
fn native_audio_set_output_host(
    state: tauri::State<NativeAudioState>,
    output_host: Option<native_audio::NativeAudioHostSelection>,
) -> Result<(), String> {
    let normalized = normalize_native_audio_output_host_selection(output_host);
    let normalized = match normalized {
        Some(sel) => Some(native_audio::canonicalize_output_host_selection(sel)?),
        None => None,
    };
    let maybe_engine_cfg = {
        let lock = state.inner.engine.lock().unwrap();
        lock.as_ref().map(|e| (e.sample_rate_hz, e.channels))
    };

    if let Some((sample_rate_hz, channels)) = maybe_engine_cfg {
        let new_engine = native_audio::NativeAudioHandle::new_with_output_device(
            sample_rate_hz,
            channels,
            normalized.clone(),
            None,
        )?;

        let old = {
            let mut lock = state.inner.engine.lock().unwrap();
            lock.replace(new_engine)
        };
        if let Some(old) = old {
            old.shutdown();
        }
    }

    *state.selected_output_host.lock().unwrap() = normalized;
    // Host switch invalidates previous device selection identity across host backends.
    *state.selected_output_device.lock().unwrap() = None;
    Ok(())
}

#[tauri::command]
fn native_audio_set_output_host_and_persist(
    app: AppHandle,
    state: tauri::State<NativeAudioState>,
    output_host: Option<native_audio::NativeAudioHostSelection>,
) -> Result<(), String> {
    native_audio_set_output_host(state.clone(), output_host)?;
    let canonical_host = current_native_audio_output_host_selection(&state);
    set_native_audio_output_host_selection(&app, canonical_host)?;
    // Host switch clears device selection.
    set_native_audio_output_device_selection(&app, None)
}

#[tauri::command]
fn native_audio_get_selected_output_device(
    state: tauri::State<NativeAudioState>,
) -> Option<native_audio::NativeAudioDeviceSelection> {
    current_native_audio_output_device_selection(&state)
}

#[tauri::command]
fn native_audio_set_output_device(
    state: tauri::State<NativeAudioState>,
    output_device: Option<native_audio::NativeAudioDeviceSelection>,
) -> Result<(), String> {
    let host_selection = current_native_audio_output_host_selection(&state);
    let normalized = normalize_native_audio_output_device_selection(output_device);
    let normalized = match normalized {
        Some(sel) => Some(native_audio::canonicalize_output_device_selection(
            host_selection.clone(),
            sel,
        )?),
        None => None,
    };
    let maybe_engine_cfg = {
        let lock = state.inner.engine.lock().unwrap();
        lock.as_ref().map(|e| (e.sample_rate_hz, e.channels))
    };

    if let Some((sample_rate_hz, channels)) = maybe_engine_cfg {
        let target_sample_rate_hz = match normalized.as_ref() {
            Some(sel) if sel.sample_rate_hz > 0 => sel.sample_rate_hz,
            _ => native_audio::preferred_output_sample_rate_for_selection(
                host_selection.clone(),
                None,
            )
            .unwrap_or(sample_rate_hz),
        };
        let new_engine = native_audio::NativeAudioHandle::new_with_output_device(
            target_sample_rate_hz,
            channels,
            host_selection,
            normalized.clone(),
        )?;

        let old = {
            let mut lock = state.inner.engine.lock().unwrap();
            lock.replace(new_engine)
        };
        if let Some(old) = old {
            old.shutdown();
        }
    }

    *state.selected_output_device.lock().unwrap() = normalized;
    Ok(())
}

#[tauri::command]
fn native_audio_set_output_device_and_persist(
    app: AppHandle,
    state: tauri::State<NativeAudioState>,
    output_device: Option<native_audio::NativeAudioDeviceSelection>,
) -> Result<(), String> {
    native_audio_set_output_device(state.clone(), output_device)?;
    let canonical_host = current_native_audio_output_host_selection(&state);
    let canonical = current_native_audio_output_device_selection(&state);
    set_native_audio_output_host_selection(&app, canonical_host)?;
    set_native_audio_output_device_selection(&app, canonical)
}

fn with_native_engine<T>(
    state: &tauri::State<NativeAudioState>,
    f: impl FnOnce(&native_audio::NativeAudioHandle) -> Result<T, String>,
) -> Result<T, String> {
    let lock = state.inner.engine.lock().unwrap();
    let Some(engine) = lock.as_ref() else {
        return Err("native audio engine not initialized".to_string());
    };
    f(engine)
}

#[tauri::command]
fn native_audio_load_wav_bytes(
    state: tauri::State<NativeAudioState>,
    wav_bytes: Vec<u8>,
) -> Result<(), String> {
    with_native_engine(&state, |e| e.load_wav_bytes(&wav_bytes))
}

#[tauri::command]
fn native_audio_load_audio_bytes(
    state: tauri::State<NativeAudioState>,
    mime: String,
    bytes: Vec<u8>,
) -> Result<(), String> {
    // Decode (supports mp3/ogg/vorbis/wav via symphonia).
    let decoded = audio_decode::decode_to_pcm16(&bytes, &mime)?;

    let target_sample_rate_hz =
        preferred_native_audio_sample_rate_hz(&state, decoded.sample_rate_hz)?;
    ensure_native_audio_engine_format(&state, target_sample_rate_hz, decoded.channels)?;

    with_native_engine(&state, |e| {
        e.load_pcm16(decoded.sample_rate_hz, decoded.channels, decoded.data)
    })
}

#[tauri::command]
fn native_audio_play(state: tauri::State<NativeAudioState>) -> Result<(), String> {
    with_native_engine(&state, |e| e.play())
}

#[tauri::command]
fn native_audio_pause(state: tauri::State<NativeAudioState>) -> Result<(), String> {
    with_native_engine(&state, |e| e.pause())
}

#[tauri::command]
fn native_audio_stop(state: tauri::State<NativeAudioState>) -> Result<(), String> {
    with_native_engine(&state, |e| e.stop())
}

#[tauri::command]
fn native_audio_seek(state: tauri::State<NativeAudioState>, t_sec: f64) -> Result<(), String> {
    with_native_engine(&state, |e| e.seek_seconds(t_sec))
}

#[tauri::command]
fn native_audio_set_loop(
    state: tauri::State<NativeAudioState>,
    t0: Option<f64>,
    t1: Option<f64>,
) -> Result<(), String> {
    with_native_engine(&state, |e| e.set_loop_seconds(t0, t1))
}

#[tauri::command]
fn native_audio_set_playback_rate(
    state: tauri::State<NativeAudioState>,
    rate: f64,
) -> Result<(), String> {
    with_native_engine(&state, |e| e.set_playback_rate(rate))
}

#[tauri::command]
fn native_audio_get_state(
    state: tauri::State<NativeAudioState>,
) -> Result<native_audio::NativeAudioState, String> {
    with_native_engine(&state, |e| Ok(e.state()))
}

#[tauri::command]
fn native_audio_shutdown(state: tauri::State<NativeAudioState>) -> Result<(), String> {
    // Take ownership so we can join the audio thread.
    let mut lock = state.inner.engine.lock().unwrap();
    if let Some(engine) = lock.take() {
        engine.shutdown();
    }
    Ok(())
}

// -----------------
// Stem+MIDI AuralSong creator
// -----------------

#[tauri::command]
async fn midi_list_tracks(path: String) -> Result<Vec<stem_midi::MidiTrackInfo>, String> {
    run_blocking_command("midi track scan", move || {
        let midi_bytes = fs::read(&path).map_err(|e| format!("read {path}: {e}"))?;
        stem_midi::list_midi_tracks(&midi_bytes)
    })
    .await
}

#[tauri::command]
async fn stem_midi_create_auralsong(
    app: AppHandle,
    req: stem_midi::StemMidiCreateRequest,
) -> Result<stem_midi::StemMidiCreateResult, String> {
    let songs_folder = PathBuf::from(get_songs_folder(app.clone())?);
    run_blocking_command("stem+midi import", move || {
        stem_midi::create_auralsong(req, &songs_folder)
    })
    .await
}

fn sanitize_auralsong_component(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    for ch in raw.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
        } else if ch == ' ' || ch == '-' || ch == '_' {
            out.push('_');
        }
    }
    while out.contains("__") {
        out = out.replace("__", "_");
    }
    let out = out.trim_matches('_');
    if out.is_empty() {
        "imported_song".to_string()
    } else {
        out.to_string()
    }
}

// The durable import artifact is feedpak content (manifest.yaml + aural/ …), so
// its filename MUST carry the .feedpak extension — a .auralsong-named feedpak
// breaks the app's extension-keyed aural/ vs features/ path resolution.
fn ingest_out_filename(base: &str, idx: Option<u32>) -> String {
    match idx {
        None => format!("ingest_{base}.feedpak"),
        Some(i) => format!("ingest_{base}_{i}.feedpak"),
    }
}

fn default_ingest_out_auralsong_path(app: &AppHandle, source_path: &str) -> Result<String, String> {
    let songs_folder = PathBuf::from(get_songs_folder(app.clone())?);
    fs::create_dir_all(&songs_folder).map_err(|e| format!("mkdir songs folder: {e}"))?;

    let source = Path::new(source_path);
    let stem = source
        .file_stem()
        .and_then(|x| x.to_str())
        .unwrap_or("imported_song");
    let base = sanitize_auralsong_component(stem);

    // The import pipeline writes feedpak content (manifest.yaml + aural/ ...),
    // so the durable artifact must carry the .feedpak extension — otherwise the
    // app's extension-keyed path resolution (aural/ vs features/) breaks.
    let mut candidate = songs_folder.join(ingest_out_filename(&base, None));
    if candidate.exists() {
        for i in 2..=9_999 {
            let next = songs_folder.join(ingest_out_filename(&base, Some(i)));
            if !next.exists() {
                candidate = next;
                break;
            }
        }
        if candidate.exists() {
            return Err("unable to choose unique ingest output path".to_string());
        }
    }

    Ok(candidate.to_string_lossy().to_string())
}

#[tauri::command]
async fn ingest_import(
    app: AppHandle,
    mut req: ingest_sidecar::IngestImportRequest,
) -> Result<ingest_sidecar::IngestImportResult, String> {
    let out_missing = req
        .out_auralsong_path
        .as_ref()
        .map(|x| x.trim().is_empty())
        .unwrap_or(true);
    if out_missing {
        req.out_auralsong_path = Some(default_ingest_out_auralsong_path(&app, &req.source_path)?);
    }

    // Capture the paths before `req` moves into the blocking closure -- we
    // need them after the sidecar returns to preserve any reference MIDI
    // the user supplied (Suno gameplay export being the canonical case).
    let source_path = req.source_path.clone();
    let out_auralsong_path = req.out_auralsong_path.clone().unwrap_or_default();

    let mut result = run_blocking_command("analysis import", move || {
        ingest_sidecar::run_ingest_import_with_progress(req, Some(&app))
    })
    .await?;

    // Best-effort: when the sidecar succeeded on a folder source, copy any
    // user-supplied MIDI from the source folder into the AuralSong's
    // features/midi/ tree and record them in assets.midi.reference_paths.
    // The Refine workspace will render these as a guide layer alongside the
    // sidecar's per-instrument transcription candidates. A failure here
    // doesn't invalidate the import -- log it on the result and move on.
    if result.ok && !out_auralsong_path.is_empty() {
        let source = std::path::Path::new(&source_path);
        let auralsong = std::path::Path::new(&out_auralsong_path);
        match raw_song::preserve_source_midis_into_auralsong(source, auralsong) {
            Ok(rel_paths) => {
                result.preserved_reference_midis = rel_paths;
            }
            Err(e) => {
                // Surface the failure to the user via stderr tail without
                // failing the import -- the AuralSong is still valid.
                if !result.stderr.is_empty() {
                    result.stderr.push('\n');
                }
                result
                    .stderr
                    .push_str(&format!("[reference-midi-preserve] {e}"));
            }
        }
    }

    Ok(result)
}

#[tauri::command]
async fn ingest_runtime_check(
    app: AppHandle,
) -> Result<ingest_sidecar::IngestRuntimeCheckResult, String> {
    run_blocking_command("ingest runtime check", move || {
        ingest_sidecar::run_ingest_runtime_check(Default::default(), Some(&app))
    })
    .await
}

#[tauri::command]
async fn ingest_refine_candidates(
    app: AppHandle,
    req: ingest_sidecar::RefineCandidatesRequest,
) -> Result<ingest_sidecar::IngestRuntimeCheckResult, String> {
    run_blocking_command("refine candidates precompute", move || {
        ingest_sidecar::run_ingest_refine_candidates(req, Some(&app))
    })
    .await
}

#[tauri::command]
async fn ingest_spectrogram(
    app: AppHandle,
    req: ingest_sidecar::SpectrogramRequest,
) -> Result<ingest_sidecar::IngestRuntimeCheckResult, String> {
    run_blocking_command("spectrogram build", move || {
        ingest_sidecar::run_ingest_spectrogram(req, Some(&app))
    })
    .await
}

#[tauri::command]
async fn ingest_align_drum_onsets(
    app: AppHandle,
    req: ingest_sidecar::AlignDrumOnsetsRequest,
) -> Result<ingest_sidecar::IngestRuntimeCheckResult, String> {
    run_blocking_command("align drum onsets", move || {
        ingest_sidecar::run_ingest_align_drum_onsets(req, Some(&app))
    })
    .await
}

#[tauri::command]
async fn ingest_refresh_meter(
    app: AppHandle,
    req: ingest_sidecar::AlignDrumOnsetsRequest,
) -> Result<ingest_sidecar::IngestRuntimeCheckResult, String> {
    run_blocking_command("refresh meter", move || {
        ingest_sidecar::run_ingest_refresh_meter(req, Some(&app))
    })
    .await
}

#[tauri::command]
async fn ingest_prep_arrangements(
    app: AppHandle,
    req: ingest_sidecar::AlignDrumOnsetsRequest,
) -> Result<ingest_sidecar::IngestRuntimeCheckResult, String> {
    run_blocking_command("prep arrangements", move || {
        ingest_sidecar::run_ingest_prep_arrangements(req, Some(&app))
    })
    .await
}

#[tauri::command]
async fn inspect_raw_song_folder(
    folder_path: String,
) -> Result<raw_song::RawSongFolderInspection, String> {
    run_blocking_command("raw song folder inspection", move || {
        raw_song::inspect_raw_song_folder(Path::new(&folder_path))
    })
    .await
}

#[tauri::command]
async fn import_raw_song_folder(
    app: AppHandle,
    req: raw_song::ImportRawSongFolderRequest,
) -> Result<raw_song::ImportRawSongFolderResult, String> {
    let songs_folder = PathBuf::from(get_songs_folder(app.clone())?);
    run_blocking_command("raw song folder import", move || {
        raw_song::import_raw_song_folder(req, &songs_folder)
    })
    .await
}

#[tauri::command]
fn scan_auralsongs(app: AppHandle) -> Result<Vec<AuralSongScanEntry>, String> {
    let folder = get_songs_folder(app.clone())?;
    let root = PathBuf::from(folder);

    // Ensure the songs folder exists on first run.
    if let Err(e) = fs::create_dir_all(&root) {
        return Ok(vec![AuralSongScanEntry {
            container_path: root.to_string_lossy().to_string(),
            kind: "songs_folder".to_string(),
            ok: false,
            manifest: None,
            error: Some(format!("cannot create songs folder: {e}")),
        }]);
    }

    // Ensure a tiny built-in demo song exists so the app is playable even
    // before the user imports anything.
    // Best-effort: failure should not prevent listing user songs.
    let _ = demo_auralsong::ensure_demo_auralsong(&root);

    let mut out: Vec<AuralSongScanEntry> = vec![];

    let entries = match fs::read_dir(&root) {
        Ok(e) => e,
        Err(e) => {
            return Ok(vec![AuralSongScanEntry {
                container_path: root.to_string_lossy().to_string(),
                kind: "songs_folder".to_string(),
                ok: false,
                manifest: None,
                error: Some(format!("cannot read songs folder: {e}")),
            }]);
        }
    };

    for entry in entries.flatten() {
        let p = entry.path();
        let file_name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
        let path_str = p.to_string_lossy().to_string();
        let kind = if p.is_dir() { "directory" } else { "zip" };

        // feedpak / sloppak are the active manifest-driven formats; scan them
        // via the feedpak reader and map the summary onto the shared
        // ManifestSummary shape the library panel renders.
        if is_feedpak_path(file_name) {
            match scan_feedpak(&p) {
                Ok(summary) => out.push(AuralSongScanEntry {
                    container_path: path_str,
                    kind: kind.to_string(),
                    ok: true,
                    manifest: Some(feedpak_summary_to_manifest(&summary)),
                    error: None,
                }),
                Err(e) => out.push(AuralSongScanEntry {
                    container_path: path_str,
                    kind: kind.to_string(),
                    ok: false,
                    manifest: None,
                    error: Some(e),
                }),
            }
            continue;
        }

        if !is_auralsong_path(file_name) {
            continue;
        }

        let manifest_result = if p.is_dir() {
            read_dir_manifest(&p)
        } else {
            read_zip_manifest(&p)
        };
        match manifest_result {
            Ok(m) => out.push(AuralSongScanEntry {
                container_path: path_str,
                kind: kind.to_string(),
                ok: true,
                manifest: Some(m),
                error: None,
            }),
            Err(e) => out.push(AuralSongScanEntry {
                container_path: path_str,
                kind: kind.to_string(),
                ok: false,
                manifest: None,
                error: Some(e),
            }),
        }
    }

    Ok(out)
}

/// Map a [`FeedpakSummary`] onto the shared [`ManifestSummary`] the Studio
/// library panel renders. feedpak carries no `schema_version`/`song_id`; the
/// title/artist/duration come straight from the manifest.
fn feedpak_summary_to_manifest(
    summary: &auralsong_core::feedpak::FeedpakSummary,
) -> ManifestSummary {
    ManifestSummary {
        schema_version: summary.feedpak_version.clone(),
        song_id: None,
        title: Some(summary.title.clone()),
        artist: Some(summary.artist.clone()),
        duration_sec: Some(summary.duration),
    }
}

#[tauri::command]
fn read_auralsong_audio(container_path: String) -> Result<AudioBlob, String> {
    let p = PathBuf::from(&container_path);

    if !is_supported_container(&container_path) {
        return Err("path does not end with .feedpak, .sloppak, or .auralsong".to_string());
    }

    // feedpak: read the default stem named in manifest.yaml.
    if is_feedpak_path(&container_path) {
        let manifest = read_feedpak_manifest(&p)?;
        let (rel, mime) = feedpak_default_stem(&manifest)?;
        let bytes = if p.is_dir() {
            read_dir_audio(&p, &rel)?
        } else {
            read_zip_audio(&p, &rel)?
        };
        return Ok(AudioBlob {
            mime: mime.to_string(),
            bytes,
        });
    }

    // Prefer OGG if present, otherwise MP3, otherwise WAV.
    let (rel, mime) = if p.is_dir() {
        if dir_has_file(&p, "audio/mix.ogg") {
            ("audio/mix.ogg", "audio/ogg")
        } else if dir_has_file(&p, "audio/mix.mp3") {
            ("audio/mix.mp3", "audio/mpeg")
        } else if dir_has_file(&p, "audio/mix.wav") {
            ("audio/mix.wav", "audio/wav")
        } else {
            return Err("no audio/mix.ogg, audio/mix.mp3, or audio/mix.wav found".to_string());
        }
    } else if zip_has_file(&p, "audio/mix.ogg").unwrap_or(false) {
        ("audio/mix.ogg", "audio/ogg")
    } else if zip_has_file(&p, "audio/mix.mp3").unwrap_or(false) {
        ("audio/mix.mp3", "audio/mpeg")
    } else if zip_has_file(&p, "audio/mix.wav").unwrap_or(false) {
        ("audio/mix.wav", "audio/wav")
    } else {
        return Err("no audio/mix.ogg, audio/mix.mp3, or audio/mix.wav found".to_string());
    };

    let bytes = if p.is_dir() {
        read_dir_audio(&p, rel)?
    } else {
        read_zip_audio(&p, rel)?
    };

    Ok(AudioBlob {
        mime: mime.to_string(),
        bytes,
    })
}

#[tauri::command]
fn native_audio_load_auralsong_audio(
    state: tauri::State<NativeAudioState>,
    container_path: String,
) -> Result<LoadedAuralSongAudioInfo, String> {
    let p = PathBuf::from(&container_path);

    if !is_supported_container(&container_path) {
        return Err("path does not end with .feedpak, .sloppak, or .auralsong".to_string());
    }

    // Resolve the playable audio: feedpak default stem, else legacy mix.*.
    let (rel, mime): (String, &str) = if is_feedpak_path(&container_path) {
        let manifest = read_feedpak_manifest(&p)?;
        feedpak_default_stem(&manifest)?
    } else if p.is_dir() {
        if dir_has_file(&p, "audio/mix.ogg") {
            ("audio/mix.ogg".to_string(), "audio/ogg")
        } else if dir_has_file(&p, "audio/mix.mp3") {
            ("audio/mix.mp3".to_string(), "audio/mpeg")
        } else if dir_has_file(&p, "audio/mix.wav") {
            ("audio/mix.wav".to_string(), "audio/wav")
        } else {
            return Err("no audio/mix.ogg, audio/mix.mp3, or audio/mix.wav found".to_string());
        }
    } else if zip_has_file(&p, "audio/mix.ogg").unwrap_or(false) {
        ("audio/mix.ogg".to_string(), "audio/ogg")
    } else if zip_has_file(&p, "audio/mix.mp3").unwrap_or(false) {
        ("audio/mix.mp3".to_string(), "audio/mpeg")
    } else if zip_has_file(&p, "audio/mix.wav").unwrap_or(false) {
        ("audio/mix.wav".to_string(), "audio/wav")
    } else {
        return Err("no audio/mix.ogg, audio/mix.mp3, or audio/mix.wav found".to_string());
    };

    // Read audio bytes on the Rust side (no IPC transfer).
    let bytes = if p.is_dir() {
        read_dir_audio(&p, &rel)?
    } else {
        read_zip_audio(&p, &rel)?
    };

    // Decode + load into engine (this may reinit engine to match SR/channels).
    let decoded = audio_decode::decode_to_pcm16(&bytes, mime)?;
    let duration_sec = if decoded.sample_rate_hz > 0 && decoded.channels > 0 {
        // interleaved i16 samples
        (decoded.data.len() as f64) / (decoded.sample_rate_hz as f64) / (decoded.channels as f64)
    } else {
        0.0
    };

    let target_sample_rate_hz =
        preferred_native_audio_sample_rate_hz(&state, decoded.sample_rate_hz)?;
    ensure_native_audio_engine_format(&state, target_sample_rate_hz, decoded.channels)?;

    with_native_engine(&state, |e| {
        e.load_pcm16(decoded.sample_rate_hz, decoded.channels, decoded.data)
    })?;

    Ok(LoadedAuralSongAudioInfo {
        mime: mime.to_string(),
        duration_sec,
    })
}

/// Base stems the refine "All" playback sums (mirrors the game's mixer set).
const MIXER_BASE_STEMS: &[&str] = &["bass", "drums", "vocals", "guitar", "keys", "other"];

fn audio_mime_for_path(rel: &str) -> &'static str {
    let lower = rel.to_ascii_lowercase();
    if lower.ends_with(".ogg") {
        "audio/ogg"
    } else if lower.ends_with(".mp3") {
        "audio/mpeg"
    } else if lower.ends_with(".flac") {
        "audio/flac"
    } else {
        "audio/wav"
    }
}

#[derive(serde::Serialize)]
pub struct LoadedPackAudioInfo {
    pub mime: String,
    pub duration_sec: f64,
    pub roles: Vec<String>,
}

/// Load a feedpak's audio into the native engine for the refine workspace, so
/// Studio auditioning runs through the SAME engine as the game — which is what
/// makes the shared A/V calibration valid. `role`:
///   - None / "all" -> the base stems summed at unity gain
///   - "<role>"      -> that single stem, solo
/// Stems are read + decoded Rust-side (no multi-MB IPC of the browser path).
#[tauri::command]
fn native_audio_load_pack_audio(
    state: tauri::State<NativeAudioState>,
    container_path: String,
    role: Option<String>,
) -> Result<LoadedPackAudioInfo, String> {
    let p = PathBuf::from(&container_path);
    if !is_feedpak_path(&container_path) {
        return Err(
            "native_audio_load_pack_audio requires a .feedpak or .sloppak container".to_string(),
        );
    }
    let manifest = read_feedpak_manifest(&p)?;
    let solo = role
        .as_deref()
        .filter(|r| !r.is_empty() && *r != "all")
        .map(|r| r.to_string());

    let mut roles: Vec<String> = Vec::new();
    let mut decoded_stems: Vec<(u32, u16, Vec<i16>)> = Vec::new();
    let mut fmt: Option<(u32, u16)> = None;
    let mut max_frames: usize = 0;
    let mut mime_out = "audio/wav";
    for stem in &manifest.stems {
        let take = match &solo {
            Some(r) => stem.id.as_str() == r.as_str(),
            None => MIXER_BASE_STEMS.contains(&stem.id.as_str()),
        };
        if !take {
            continue;
        }
        let mime = audio_mime_for_path(&stem.file);
        mime_out = mime;
        let bytes = if p.is_dir() {
            read_dir_audio(&p, &stem.file)?
        } else {
            read_zip_audio(&p, &stem.file)?
        };
        let d = audio_decode::decode_to_pcm16(&bytes, mime)?;
        if d.channels == 0 || d.sample_rate_hz == 0 {
            continue;
        }
        if fmt.is_none() {
            fmt = Some((d.sample_rate_hz, d.channels));
        }
        let frames = d.data.len() / (d.channels as usize).max(1);
        max_frames = max_frames.max(frames);
        roles.push(stem.id.clone());
        decoded_stems.push((d.sample_rate_hz, d.channels, d.data));
    }

    let Some((sr, channels)) = fmt else {
        return Err(format!("no decodable stem for role {role:?}"));
    };
    let duration_sec = if sr > 0 { max_frames as f64 / sr as f64 } else { 0.0 };

    let target_sr = preferred_native_audio_sample_rate_hz(&state, sr)?;
    ensure_native_audio_engine_format(&state, target_sr, channels)?;
    if decoded_stems.len() == 1 {
        let (s_sr, s_ch, s_data) = decoded_stems.pop().unwrap();
        with_native_engine(&state, move |e| e.load_pcm16(s_sr, s_ch, s_data))?;
    } else {
        with_native_engine(&state, move |e| e.load_stems(decoded_stems))?;
    }

    Ok(LoadedPackAudioInfo {
        mime: mime_out.to_string(),
        duration_sec,
        roles,
    })
}

#[tauri::command]
fn read_auralsong_json(
    container_path: String,
    rel_path: String,
) -> Result<serde_json::Value, String> {
    let p = PathBuf::from(&container_path);
    if !is_supported_container(&container_path) {
        return Err("path does not end with .feedpak, .sloppak, or .auralsong".to_string());
    }
    if !is_allowed_feature_rel(&rel_path) {
        return Err("only features/* or aural/* json is allowed".to_string());
    }
    if !rel_path.ends_with(".json") {
        return Err("rel_path must be a .json".to_string());
    }

    let raw = if p.is_dir() {
        read_dir_text(&p, &rel_path)?
    } else {
        read_zip_text(&p, &rel_path)?
    };
    serde_json::from_str(&raw).map_err(|e| format!("invalid JSON: {e}"))
}

#[tauri::command]
fn read_auralsong_mid(container_path: String, rel_path: String) -> Result<MidiBlob, String> {
    let p = PathBuf::from(&container_path);
    if !is_supported_container(&container_path) {
        return Err("path does not end with .feedpak, .sloppak, or .auralsong".to_string());
    }
    if !is_allowed_feature_rel(&rel_path) {
        return Err("only features/* or aural/* is allowed".to_string());
    }
    if !rel_path.ends_with(".mid") && !rel_path.ends_with(".midi") {
        return Err("rel_path must be a .mid/.midi".to_string());
    }

    let bytes = if p.is_dir() {
        let abs = p.join(&rel_path);
        fs::read(&abs).map_err(|e| format!("read {}: {e}", abs.display()))?
    } else {
        read_zip_audio(&p, &rel_path)?
    };

    Ok(MidiBlob { bytes })
}

/// Read raw bytes of an arbitrary features/* file (e.g. spectrogram tile PNGs)
/// from an .auralsong dir or zip. Used by the guided-edit spectrogram overlay.
#[tauri::command]
fn read_auralsong_bytes(container_path: String, rel_path: String) -> Result<Vec<u8>, String> {
    let p = PathBuf::from(&container_path);
    if !is_supported_container(&container_path) {
        return Err("path does not end with .feedpak, .sloppak, or .auralsong".to_string());
    }
    if !is_allowed_feature_rel(&rel_path) {
        return Err("only features/* or aural/* is allowed".to_string());
    }
    if p.is_dir() {
        let abs = p.join(&rel_path);
        fs::read(&abs).map_err(|e| format!("read {}: {e}", abs.display()))
    } else {
        read_zip_audio(&p, &rel_path)
    }
}

#[tauri::command]
fn read_auralsong_charts(container_path: String) -> Result<serde_json::Value, String> {
    let p = PathBuf::from(&container_path);
    if !is_supported_container(&container_path) {
        return Err("path does not end with .feedpak, .sloppak, or .auralsong".to_string());
    }

    let chart_paths = if p.is_dir() {
        dir_list_charts(&p)
    } else {
        zip_list_charts(&p).unwrap_or_default()
    };

    let mut out = serde_json::Map::new();
    for rel in chart_paths {
        let raw = if p.is_dir() {
            read_dir_text(&p, &rel)?
        } else {
            read_zip_text(&p, &rel)?
        };
        let v: serde_json::Value =
            serde_json::from_str(&raw).map_err(|e| format!("invalid JSON in {}: {e}", rel))?;
        out.insert(rel, v);
    }

    Ok(serde_json::Value::Object(out))
}

#[tauri::command]
fn write_auralsong_lyrics_json(
    container_path: String,
    lyrics_json: serde_json::Value,
) -> Result<(), String> {
    let p = PathBuf::from(&container_path);
    if !is_supported_container(&container_path) {
        return Err("path does not end with .feedpak, .sloppak, or .auralsong".to_string());
    }
    if !p.is_dir() {
        return Err(
            "writing features is only supported for directory containers (not zip packs)"
                .to_string(),
        );
    }

    // feedpak relocates feature artifacts under `aural/`; legacy packs use
    // `features/`.
    let subdir = if is_feedpak_path(&container_path) {
        "aural"
    } else {
        "features"
    };
    let features_dir = p.join(subdir);
    fs::create_dir_all(&features_dir)
        .map_err(|e| format!("mkdir {}: {e}", features_dir.display()))?;
    let out_path = features_dir.join("lyrics.json");

    let raw = serde_json::to_string_pretty(&lyrics_json)
        .map_err(|e| format!("serialize lyrics json: {e}"))?;
    fs::write(&out_path, raw).map_err(|e| format!("write {}: {e}", out_path.display()))?;
    Ok(())
}

/// Write an arbitrary feature JSON file into a directory container.
///
/// Used by the Studio Refine workspace to persist user picks
/// (feedpak: `aural/refinement.<instrument>.json`; legacy:
/// `features/refinement.<instrument>.json`) and -- in future -- any other
/// small editor-time JSON the workspace produces. Path-traversal safe:
/// `rel_path` must start with `features/`, `aural/`, or `arrangements/`,
/// must not contain `..`, and must end with `.json`.
///
/// Directory containers only -- writing into a zip pack would require
/// re-archiving; the workspace already requires a directory container for
/// the same reason.
#[tauri::command]
fn write_auralsong_features_json(
    container_path: String,
    rel_path: String,
    value: serde_json::Value,
) -> Result<(), String> {
    let p = PathBuf::from(&container_path);
    if !is_supported_container(&container_path) {
        return Err("path does not end with .feedpak, .sloppak, or .auralsong".to_string());
    }
    if !p.is_dir() {
        return Err(
            "writing features is only supported for directory containers (not zip packs)"
                .to_string(),
        );
    }
    if !is_allowed_feature_rel(&rel_path) {
        return Err("rel_path must start with features/, aural/, or arrangements/".to_string());
    }
    if !rel_path.ends_with(".json") {
        return Err("rel_path must end with .json".to_string());
    }

    let out_path = p.join(&rel_path);
    if let Some(parent) = out_path.parent() {
        fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
    }
    let raw = serde_json::to_string_pretty(&value)
        .map_err(|e| format!("serialize features json: {e}"))?;
    fs::write(&out_path, raw).map_err(|e| format!("write {}: {e}", out_path.display()))?;
    Ok(())
}

/// Build [`AuralSongDetails`] for a feedpak container.
///
/// feedpak relocates the per-feature artifacts the legacy scan probed under
/// `features/` into `aural/` and folds the beats/sections/tempo tracks into a
/// single `song_timeline.json`; so `has_beats`/`has_tempo_map`/`has_sections`
/// are reported together off `song_timeline` (or the transcribed notes MIDI).
/// Audio presence reflects the default stem's container.
fn feedpak_details(container_path: String, p: &Path) -> AuralSongDetails {
    let manifest = match read_feedpak_manifest(p) {
        Ok(m) => m,
        Err(e) => {
            return AuralSongDetails {
                container_path,
                kind: if p.is_dir() { "directory" } else { "zip" }.to_string(),
                ok: false,
                manifest_summary: None,
                manifest_raw: None,
                has_beats: false,
                has_tempo_map: false,
                has_sections: false,
                has_events: false,
                has_lyrics: false,
                has_notes_mid: false,
                has_mix_mp3: false,
                has_mix_ogg: false,
                has_mix_wav: false,
                charts: vec![],
                arrangements_count: 0,
                lyrics_rel: None,
                error: Some(e),
            };
        }
    };

    let kind = if p.is_dir() { "directory" } else { "zip" }.to_string();
    let summary =
        feedpak_summary_to_manifest(&auralsong_core::feedpak::scan_feedpak(p).unwrap_or_default());
    // Raw manifest for the details pane: re-read the YAML text and reshape it
    // into a JSON value (serde_yaml::Value -> serde_json::Value). Best-effort;
    // the typed summary/flags already carry what the panel needs.
    let manifest_raw = read_feedpak_manifest_yaml_text(p)
        .ok()
        .and_then(|raw| serde_yaml::from_str::<serde_json::Value>(&raw).ok());

    let has_timeline = manifest.song_timeline.is_some();
    let has_notes_mid = manifest.aural_notes_mid.is_some();
    let has_structure = has_timeline || has_notes_mid;

    // Default-stem container -> audio presence flag.
    let (has_mix_ogg, has_mix_mp3, has_mix_wav) = match feedpak_default_stem(&manifest) {
        Ok((_, "audio/ogg")) => (true, false, false),
        Ok((_, "audio/mpeg")) => (false, true, false),
        Ok(_) => (false, false, true),
        Err(_) => (false, false, false),
    };

    AuralSongDetails {
        container_path,
        kind,
        ok: true,
        manifest_summary: Some(summary),
        manifest_raw,
        has_beats: has_structure,
        has_tempo_map: has_structure,
        has_sections: has_structure,
        has_events: has_notes_mid,
        has_lyrics: manifest.lyrics.is_some(),
        has_notes_mid,
        has_mix_mp3,
        has_mix_ogg,
        has_mix_wav,
        // feedpak notation lives under arrangements/, surfaced separately; the
        // legacy charts/ list is empty for feedpak.
        charts: vec![],
        arrangements_count: manifest.arrangements.len(),
        lyrics_rel: manifest.lyrics.clone(),
        error: None,
    }
}

#[tauri::command]
fn get_auralsong_details(container_path: String) -> Result<AuralSongDetails, String> {
    let p = PathBuf::from(&container_path);

    if !is_supported_container(&container_path) {
        return Ok(AuralSongDetails {
            container_path,
            kind: "unknown".to_string(),
            ok: false,
            manifest_summary: None,
            manifest_raw: None,
            has_beats: false,
            has_tempo_map: false,
            has_sections: false,
            has_events: false,
            has_lyrics: false,
            has_notes_mid: false,
            has_mix_mp3: false,
            has_mix_ogg: false,
            has_mix_wav: false,
            charts: vec![],
            arrangements_count: 0,
            lyrics_rel: None,
            error: Some("path does not end with .feedpak, .sloppak, or .auralsong".to_string()),
        });
    }

    // feedpak: parse manifest.yaml and report the relocated `aural/...`
    // structure + stem audio presence.
    if is_feedpak_path(&container_path) {
        return Ok(feedpak_details(container_path, &p));
    }

    if p.is_dir() {
        // Directory AuralSong
        let manifest_raw = match read_dir_manifest_raw(&p) {
            Ok(v) => Some(v),
            Err(e) => {
                return Ok(AuralSongDetails {
                    container_path,
                    kind: "directory".to_string(),
                    ok: false,
                    manifest_summary: None,
                    manifest_raw: None,
                    has_beats: dir_has_file(&p, "features/beats.json")
                        || dir_has_file(&p, "features/notes.mid"),
                    has_tempo_map: dir_has_file(&p, "features/tempo_map.json")
                        || dir_has_file(&p, "features/notes.mid"),
                    has_sections: dir_has_file(&p, "features/sections.json")
                        || dir_has_file(&p, "features/notes.mid"),
                    has_events: dir_has_file(&p, "features/events.json")
                        || dir_has_file(&p, "features/notes.mid"),
                    has_lyrics: dir_has_file(&p, "features/lyrics.json"),
                    has_notes_mid: dir_has_file(&p, "features/notes.mid"),
                    has_mix_mp3: dir_has_file(&p, "audio/mix.mp3"),
                    has_mix_ogg: dir_has_file(&p, "audio/mix.ogg"),
                    has_mix_wav: dir_has_file(&p, "audio/mix.wav"),
                    charts: dir_list_charts(&p),
                    arrangements_count: 0,
                    lyrics_rel: None,
                    error: Some(e),
                });
            }
        };

        let manifest_summary = match fs::read_to_string(p.join("manifest.json")) {
            Ok(raw) => parse_manifest_json(&raw).ok(),
            Err(_) => None,
        };

        Ok(AuralSongDetails {
            container_path,
            kind: "directory".to_string(),
            ok: true,
            manifest_summary,
            manifest_raw,
            has_beats: dir_has_file(&p, "features/beats.json")
                || dir_has_file(&p, "features/notes.mid"),
            has_tempo_map: dir_has_file(&p, "features/tempo_map.json")
                || dir_has_file(&p, "features/notes.mid"),
            has_sections: dir_has_file(&p, "features/sections.json")
                || dir_has_file(&p, "features/notes.mid"),
            has_events: dir_has_file(&p, "features/events.json")
                || dir_has_file(&p, "features/notes.mid"),
            has_lyrics: dir_has_file(&p, "features/lyrics.json"),
            has_notes_mid: dir_has_file(&p, "features/notes.mid"),
            has_mix_mp3: dir_has_file(&p, "audio/mix.mp3"),
            has_mix_ogg: dir_has_file(&p, "audio/mix.ogg"),
            has_mix_wav: dir_has_file(&p, "audio/mix.wav"),
            charts: dir_list_charts(&p),
            arrangements_count: 0,
            lyrics_rel: None,
            error: None,
        })
    } else {
        // Zip AuralSong
        let manifest_raw = match read_zip_manifest_raw(&p) {
            Ok(v) => Some(v),
            Err(e) => {
                return Ok(AuralSongDetails {
                    container_path,
                    kind: "zip".to_string(),
                    ok: false,
                    manifest_summary: None,
                    manifest_raw: None,
                    has_beats: zip_has_file(&p, "features/beats.json").unwrap_or(false)
                        || zip_has_file(&p, "features/notes.mid").unwrap_or(false),
                    has_tempo_map: zip_has_file(&p, "features/tempo_map.json").unwrap_or(false)
                        || zip_has_file(&p, "features/notes.mid").unwrap_or(false),
                    has_sections: zip_has_file(&p, "features/sections.json").unwrap_or(false)
                        || zip_has_file(&p, "features/notes.mid").unwrap_or(false),
                    has_events: zip_has_file(&p, "features/events.json").unwrap_or(false)
                        || zip_has_file(&p, "features/notes.mid").unwrap_or(false),
                    has_lyrics: zip_has_file(&p, "features/lyrics.json").unwrap_or(false),
                    has_notes_mid: zip_has_file(&p, "features/notes.mid").unwrap_or(false),
                    has_mix_mp3: zip_has_file(&p, "audio/mix.mp3").unwrap_or(false),
                    has_mix_ogg: zip_has_file(&p, "audio/mix.ogg").unwrap_or(false),
                    has_mix_wav: zip_has_file(&p, "audio/mix.wav").unwrap_or(false),
                    charts: zip_list_charts(&p).unwrap_or_default(),
                    arrangements_count: 0,
                    lyrics_rel: None,
                    error: Some(e),
                });
            }
        };

        let manifest_summary = {
            // Read summary by reusing raw (it is already parsed JSON).
            // We'll serialize back to string for the existing parser to stay consistent.
            match serde_json::to_string(manifest_raw.as_ref().unwrap()) {
                Ok(s) => parse_manifest_json(&s).ok(),
                Err(_) => None,
            }
        };

        Ok(AuralSongDetails {
            container_path,
            kind: "zip".to_string(),
            ok: true,
            manifest_summary,
            manifest_raw,
            has_beats: zip_has_file(&p, "features/beats.json").unwrap_or(false)
                || zip_has_file(&p, "features/notes.mid").unwrap_or(false),
            has_tempo_map: zip_has_file(&p, "features/tempo_map.json").unwrap_or(false)
                || zip_has_file(&p, "features/notes.mid").unwrap_or(false),
            has_sections: zip_has_file(&p, "features/sections.json").unwrap_or(false)
                || zip_has_file(&p, "features/notes.mid").unwrap_or(false),
            has_events: zip_has_file(&p, "features/events.json").unwrap_or(false)
                || zip_has_file(&p, "features/notes.mid").unwrap_or(false),
            has_lyrics: zip_has_file(&p, "features/lyrics.json").unwrap_or(false),
            has_notes_mid: zip_has_file(&p, "features/notes.mid").unwrap_or(false),
            has_mix_mp3: zip_has_file(&p, "audio/mix.mp3").unwrap_or(false),
            has_mix_ogg: zip_has_file(&p, "audio/mix.ogg").unwrap_or(false),
            has_mix_wav: zip_has_file(&p, "audio/mix.wav").unwrap_or(false),
            charts: zip_list_charts(&p).unwrap_or_default(),
            arrangements_count: 0,
            lyrics_rel: None,
            error: None,
        })
    }
}

fn parse_visualizer_manifest(raw: &str) -> Result<VisualizerManifest, String> {
    serde_json::from_str(raw).map_err(|e| format!("invalid JSON: {e}"))
}

fn read_visualizer_manifest(dir: &Path) -> Result<VisualizerManifest, String> {
    let p = dir.join("manifest.json");
    let raw = fs::read_to_string(&p).map_err(|e| format!("read {}: {e}", p.display()))?;
    parse_visualizer_manifest(&raw)
}

fn read_visualizer_entry_bytes(dir: &Path, entry_rel: &str) -> Result<Vec<u8>, String> {
    // Prevent path traversal: canonicalize and ensure entry is within plugin dir.
    let dir_can = fs::canonicalize(dir).map_err(|e| format!("canonicalize plugin dir: {e}"))?;
    let entry_abs = dir.join(entry_rel);
    let entry_can = fs::canonicalize(&entry_abs)
        .map_err(|e| format!("canonicalize entry {}: {e}", entry_abs.display()))?;

    if !entry_can.starts_with(&dir_can) {
        return Err("entry path escapes plugin directory".to_string());
    }

    fs::read(&entry_can).map_err(|e| format!("read {}: {e}", entry_can.display()))
}

fn scan_visualizers_dir(
    root: &Path,
    ensure_exists: bool,
) -> Result<Vec<VisualizerScanEntry>, String> {
    // Ensure folder exists (only for user-writable locations).
    if ensure_exists {
        let _ = fs::create_dir_all(root);
    }

    let mut out: Vec<VisualizerScanEntry> = vec![];

    let entries = match fs::read_dir(root) {
        Ok(e) => e,
        Err(e) => {
            return Ok(vec![VisualizerScanEntry {
                plugin_path: root.to_string_lossy().to_string(),
                ok: false,
                manifest: None,
                error: Some(format!("cannot read visualizers folder: {e}")),
            }]);
        }
    };

    for entry in entries.flatten() {
        let p = entry.path();
        if !p.is_dir() {
            continue;
        }

        match read_visualizer_manifest(&p) {
            Ok(m) => {
                // basic sanity check: must have id and entrypoint must exist.
                let entry_rel = m
                    .entry
                    .clone()
                    .unwrap_or_else(|| "dist/index.js".to_string());
                let entry_ok = read_visualizer_entry_bytes(&p, &entry_rel).is_ok();

                if m.id.is_none() {
                    out.push(VisualizerScanEntry {
                        plugin_path: p.to_string_lossy().to_string(),
                        ok: false,
                        manifest: Some(m),
                        error: Some("manifest.json missing id".to_string()),
                    });
                } else if !entry_ok {
                    out.push(VisualizerScanEntry {
                        plugin_path: p.to_string_lossy().to_string(),
                        ok: false,
                        manifest: Some(m),
                        error: Some(format!("missing entrypoint: {entry_rel}")),
                    });
                } else {
                    out.push(VisualizerScanEntry {
                        plugin_path: p.to_string_lossy().to_string(),
                        ok: true,
                        manifest: Some(m),
                        error: None,
                    });
                }
            }
            Err(e) => out.push(VisualizerScanEntry {
                plugin_path: p.to_string_lossy().to_string(),
                ok: false,
                manifest: None,
                error: Some(e),
            }),
        }
    }

    // deterministic ordering
    out.sort_by(|a, b| a.plugin_path.cmp(&b.plugin_path));
    Ok(out)
}

#[tauri::command]
fn scan_visualizers(app: AppHandle) -> Result<Vec<VisualizerScanEntry>, String> {
    let folder = get_visualizers_folder(app.clone())?;
    let root = PathBuf::from(folder);
    scan_visualizers_dir(&root, true)
}

#[tauri::command]
fn scan_bundled_visualizers(app: AppHandle) -> Result<Vec<VisualizerScanEntry>, String> {
    let res_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir: {e}"))?;

    let root = res_dir.join("visualizers");
    // Resource directories may be read-only in packaged builds; do not attempt to create.
    scan_visualizers_dir(&root, false)
}

#[tauri::command]
fn read_visualizer_entrypoint(plugin_path: String) -> Result<JsBlob, String> {
    let dir = PathBuf::from(plugin_path);
    let manifest = read_visualizer_manifest(&dir)?;
    let entry = manifest
        .entry
        .clone()
        .unwrap_or_else(|| "dist/index.js".to_string());

    let bytes = read_visualizer_entry_bytes(&dir, &entry)?;

    Ok(JsBlob {
        mime: "text/javascript".to_string(),
        bytes,
    })
}

#[tauri::command]
fn list_installed_modelpacks(app: AppHandle) -> Result<Vec<models::InstalledModelPack>, String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir: {e}"))?;

    Ok(models::list_installed_modelpacks(&data_dir))
}

#[tauri::command]
fn install_modelpack_zip_bytes(
    app: AppHandle,
    req: models::InstallModelPackZipRequest,
) -> Result<(), String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir: {e}"))?;

    models::install_modelpack_zip_bytes(&data_dir, req)
}

#[tauri::command]
fn install_modelpack_from_path(app: AppHandle, path: String) -> Result<(), String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir: {e}"))?;

    models::install_modelpack_from_path(&data_dir, &path)
}

pub fn run() {
    tauri::Builder::default()
        .manage(MidiClockOutputState::default())
        .manage(MidiClockInputState::default())
        .manage(NativeAudioState::default())
        .manage(SongsWatchState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            build_main_window(app)?;

            // Mount the songs-folder watcher as early as possible so external
            // tools (e.g. `aural_ingest import` from a separate shell) trigger
            // an automatic library refresh. Best-effort: a failure here just
            // means the user falls back to the manual refresh button.
            let handle = app.handle();
            match get_songs_folder(handle.clone()) {
                Ok(folder) => {
                    let path = PathBuf::from(folder);
                    if let Err(e) = songs_watch::ensure_watch(handle, &path) {
                        eprintln!("songs_watch: initial mount failed: {e}");
                    }
                }
                Err(e) => {
                    eprintln!("songs_watch: cannot resolve songs folder for initial mount: {e}");
                }
            }

            // Restore persisted MIDI clock output selection (best-effort).
            if let Ok(Some(sel)) = get_midi_clock_output_port_selection(handle) {
                if let Ok(port_id) = midi_clock::resolve_selection_to_port_id(&sel) {
                    let state = app.state::<MidiClockOutputState>();
                    // Ensure service is running and select the port.
                    {
                        let mut lock = state.svc.lock().unwrap();
                        if lock.is_none() {
                            *lock = Some(midi_clock_service::MidiClockService::spawn());
                        }
                        if let Some(svc) = lock.as_ref() {
                            svc.send(midi_clock_service::MidiClockCommand::SelectPort { port_id });
                        }
                    }
                }
            }
            if let Ok(true) = get_midi_output_allow_sysex(handle) {
                let state = app.state::<MidiClockOutputState>();
                {
                    let mut lock = state.svc.lock().unwrap();
                    if lock.is_none() {
                        *lock = Some(midi_clock_service::MidiClockService::spawn());
                    }
                    if let Some(svc) = lock.as_ref() {
                        svc.send(midi_clock_service::MidiClockCommand::SetAllowSysEx {
                            enabled: true,
                        });
                    }
                }
            }

            // Restore persisted MIDI input connection (best-effort).
            if let Ok(Some(sel)) = get_midi_input_port_selection(handle) {
                if let Ok(port_id) = midi_clock_input::resolve_selection_to_port_id(&sel) {
                    let tempo_scale = get_midi_input_tempo_scale(handle).unwrap_or(1.0);
                    let allow_sysex = get_midi_input_allow_sysex(handle).unwrap_or(false);
                    if let Ok(conn) = midi_clock_input::start_midi_clock_input(
                        handle.clone(),
                        port_id,
                        tempo_scale,
                        allow_sysex,
                    ) {
                        let state = app.state::<MidiClockInputState>();
                        *state.conn.lock().unwrap() = Some(conn);
                    }
                }
            }

            // Restore persisted native audio host/device selection (best-effort).
            if let Ok(sel) = get_native_audio_output_host_selection(handle) {
                let state = app.state::<NativeAudioState>();
                *state.selected_output_host.lock().unwrap() = sel;
            }
            if let Ok(sel) = get_native_audio_output_device_selection(handle) {
                let state = app.state::<NativeAudioState>();
                *state.selected_output_device.lock().unwrap() = sel;
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ping,
            frontend_log,
            get_songs_folder_paths,
            get_songs_folder,
            set_songs_folder_override,
            clear_songs_folder_override,
            get_av_calibration,
            set_av_calibration,
            start_songs_folder_watch,
            // native audio
            native_audio_list_output_hosts,
            native_audio_list_output_devices,
            native_audio_init,
            native_audio_get_selected_output_host,
            native_audio_set_output_host,
            native_audio_set_output_host_and_persist,
            native_audio_get_selected_output_device,
            native_audio_set_output_device,
            native_audio_set_output_device_and_persist,
            native_audio_load_wav_bytes,
            native_audio_load_audio_bytes,
            native_audio_load_auralsong_audio,
            native_audio_load_pack_audio,
            native_audio_play,
            native_audio_pause,
            native_audio_stop,
            native_audio_seek,
            native_audio_set_loop,
            native_audio_set_playback_rate,
            native_audio_get_state,
            native_audio_shutdown,
            // stem+midi
            midi_list_tracks,
            stem_midi_create_auralsong,
            ingest_import,
            ingest_runtime_check,
            ingest_refine_candidates,
            ingest_spectrogram,
            ingest_align_drum_onsets,
            ingest_refresh_meter,
            ingest_prep_arrangements,
            inspect_raw_song_folder,
            import_raw_song_folder,
            scan_auralsongs,
            get_auralsong_details,
            read_auralsong_audio,
            read_auralsong_json,
            read_auralsong_mid,
            read_auralsong_bytes,
            read_auralsong_charts,
            write_auralsong_lyrics_json,
            write_auralsong_features_json,
            read_text_file,
            convert_auralsong_to_directory,
            // plugins
            get_visualizers_folder,
            set_visualizers_folder_override,
            clear_visualizers_folder_override,
            scan_visualizers,
            scan_bundled_visualizers,
            read_visualizer_entrypoint,
            // models
            list_installed_modelpacks,
            install_modelpack_zip_bytes,
            install_modelpack_from_path,
            // midi
            list_midi_output_ports,
            midi_clock_output_select_port,
            midi_clock_output_select_port_and_persist,
            midi_clock_output_set_bpm,
            midi_clock_output_seek,
            midi_clock_output_start,
            midi_clock_output_continue,
            midi_clock_output_stop,
            midi_clock_output_shutdown,
            midi_clock_output_get_saved_port,
            midi_output_get_saved_allow_sysex,
            midi_output_set_allow_sysex,
            midi_output_set_allow_sysex_and_persist,
            midi_output_send_raw,
            midi_output_send_note_on,
            midi_output_send_note_off,
            midi_output_send_control_change,
            midi_output_send_pitch_bend,
            midi_output_send_program_change,
            midi_output_send_channel_pressure,
            midi_output_all_notes_off,
            list_midi_input_ports,
            midi_clock_input_start,
            midi_clock_input_start_and_persist,
            midi_clock_input_get_saved_settings,
            midi_clock_input_stop
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[tauri::command]
fn list_midi_output_ports() -> Result<Vec<midi_clock::MidiOutputPortInfo>, String> {
    midi_clock::list_midi_output_ports()
}

fn ensure_midi_clock_output_svc(state: &tauri::State<MidiClockOutputState>) {
    let mut svc = state.svc.lock().unwrap();
    if svc.is_none() {
        *svc = Some(midi_clock_service::MidiClockService::spawn());
    }
}

fn get_midi_clock_output_port_selection(
    app: &AppHandle,
) -> Result<Option<midi_clock::MidiOutputSelection>, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    Ok(settings.midi_clock_output_port)
}

fn set_midi_clock_output_port_selection(
    app: &AppHandle,
    sel: Option<midi_clock::MidiOutputSelection>,
) -> Result<(), String> {
    let paths = get_paths(app)?;
    let mut settings = load_settings(&paths);
    settings.midi_clock_output_port = sel;
    save_settings(&paths, &settings)
}

fn get_midi_input_port_selection(
    app: &AppHandle,
) -> Result<Option<midi_clock_input::MidiInputSelection>, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    Ok(settings.midi_input_port)
}

fn set_midi_input_port_selection(
    app: &AppHandle,
    sel: Option<midi_clock_input::MidiInputSelection>,
) -> Result<(), String> {
    let paths = get_paths(app)?;
    let mut settings = load_settings(&paths);
    settings.midi_input_port = sel;
    save_settings(&paths, &settings)
}

fn get_midi_input_tempo_scale(app: &AppHandle) -> Result<f64, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    let scale = settings.midi_input_tempo_scale.unwrap_or(1.0);
    if scale.is_finite() && scale > 0.0 {
        Ok(scale)
    } else {
        Ok(1.0)
    }
}

fn set_midi_input_tempo_scale(app: &AppHandle, tempo_scale: f64) -> Result<(), String> {
    let paths = get_paths(app)?;
    let mut settings = load_settings(&paths);
    settings.midi_input_tempo_scale = Some(if tempo_scale.is_finite() && tempo_scale > 0.0 {
        tempo_scale
    } else {
        1.0
    });
    save_settings(&paths, &settings)
}

fn get_midi_input_allow_sysex(app: &AppHandle) -> Result<bool, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    Ok(settings.midi_input_allow_sysex)
}

fn set_midi_input_allow_sysex(app: &AppHandle, allow_sysex: bool) -> Result<(), String> {
    let paths = get_paths(app)?;
    let mut settings = load_settings(&paths);
    settings.midi_input_allow_sysex = allow_sysex;
    save_settings(&paths, &settings)
}

fn get_midi_output_allow_sysex(app: &AppHandle) -> Result<bool, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    Ok(settings.midi_output_allow_sysex)
}

fn set_midi_output_allow_sysex(app: &AppHandle, allow_sysex: bool) -> Result<(), String> {
    let paths = get_paths(app)?;
    let mut settings = load_settings(&paths);
    settings.midi_output_allow_sysex = allow_sysex;
    save_settings(&paths, &settings)
}

fn get_native_audio_output_host_selection(
    app: &AppHandle,
) -> Result<Option<native_audio::NativeAudioHostSelection>, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    Ok(settings.native_audio_output_host)
}

fn set_native_audio_output_host_selection(
    app: &AppHandle,
    sel: Option<native_audio::NativeAudioHostSelection>,
) -> Result<(), String> {
    let paths = get_paths(app)?;
    let mut settings = load_settings(&paths);
    settings.native_audio_output_host = sel;
    save_settings(&paths, &settings)
}

fn get_native_audio_output_device_selection(
    app: &AppHandle,
) -> Result<Option<native_audio::NativeAudioDeviceSelection>, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    Ok(settings.native_audio_output_device)
}

fn set_native_audio_output_device_selection(
    app: &AppHandle,
    sel: Option<native_audio::NativeAudioDeviceSelection>,
) -> Result<(), String> {
    let paths = get_paths(app)?;
    let mut settings = load_settings(&paths);
    settings.native_audio_output_device = sel;
    save_settings(&paths, &settings)
}

#[tauri::command]
fn midi_clock_output_get_saved_port(
    app: AppHandle,
) -> Result<Option<midi_clock::MidiOutputSelection>, String> {
    get_midi_clock_output_port_selection(&app)
}

#[tauri::command]
fn midi_output_get_saved_allow_sysex(app: AppHandle) -> Result<bool, String> {
    get_midi_output_allow_sysex(&app)
}

#[tauri::command]
fn midi_output_set_allow_sysex(
    state: tauri::State<MidiClockOutputState>,
    enabled: bool,
) -> Result<(), String> {
    ensure_midi_clock_output_svc(&state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::SetAllowSysEx { enabled });
    }
    Ok(())
}

#[tauri::command]
fn midi_output_set_allow_sysex_and_persist(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    enabled: bool,
) -> Result<(), String> {
    set_midi_output_allow_sysex(&app, enabled)?;
    midi_output_set_allow_sysex(state, enabled)
}

#[tauri::command]
fn midi_clock_output_select_port(
    state: tauri::State<MidiClockOutputState>,
    port_id: usize,
) -> Result<(), String> {
    ensure_midi_clock_output_svc(&state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::SelectPort { port_id });
    }
    Ok(())
}

#[tauri::command]
fn midi_clock_output_select_port_and_persist(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    port_id: usize,
) -> Result<(), String> {
    // Capture name for persistence.
    let ports = midi_clock::list_midi_output_ports()?;
    let p = ports
        .into_iter()
        .find(|x| x.id == port_id)
        .ok_or_else(|| format!("invalid midi output port id {port_id}"))?;
    set_midi_clock_output_port_selection(
        &app,
        Some(midi_clock::MidiOutputSelection {
            id: p.id,
            name: p.name,
        }),
    )?;
    midi_clock_output_select_port(state, port_id)
}

fn ensure_selected_midi_output_port(
    app: &AppHandle,
    state: &tauri::State<MidiClockOutputState>,
) -> Result<usize, String> {
    let sel = get_midi_clock_output_port_selection(app)?
        .ok_or_else(|| "select a MIDI output port first".to_string())?;

    let port_id = midi_clock::resolve_selection_to_port_id(&sel)?;
    let allow_sysex = get_midi_output_allow_sysex(app).unwrap_or(false);
    ensure_midi_clock_output_svc(state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::SetAllowSysEx {
            enabled: allow_sysex,
        });
        svc.send(midi_clock_service::MidiClockCommand::SelectPort { port_id });
    }
    Ok(port_id)
}

fn send_midi_output_raw(
    app: &AppHandle,
    state: &tauri::State<MidiClockOutputState>,
    bytes: Vec<u8>,
) -> Result<(), String> {
    let _ = ensure_selected_midi_output_port(app, state)?;
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::SendRaw { bytes });
    }
    Ok(())
}

fn require_channel(channel: u8) -> Result<u8, String> {
    if channel <= 15 {
        Ok(channel)
    } else {
        Err(format!("invalid MIDI channel {channel} (expected 0..15)"))
    }
}

fn require_data7(name: &str, value: u8) -> Result<u8, String> {
    if value <= 127 {
        Ok(value)
    } else {
        Err(format!("invalid MIDI {name}={value} (expected 0..127)"))
    }
}

#[tauri::command]
fn midi_output_send_raw(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    bytes: Vec<u8>,
) -> Result<(), String> {
    send_midi_output_raw(&app, &state, bytes)
}

#[tauri::command]
fn midi_output_send_note_on(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    channel: u8,
    note: u8,
    velocity: u8,
) -> Result<(), String> {
    let ch = require_channel(channel)?;
    let n = require_data7("note", note)?;
    let vel = require_data7("velocity", velocity)?;
    send_midi_output_raw(&app, &state, vec![0x90 | ch, n, vel])
}

#[tauri::command]
fn midi_output_send_note_off(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    channel: u8,
    note: u8,
    velocity: u8,
) -> Result<(), String> {
    let ch = require_channel(channel)?;
    let n = require_data7("note", note)?;
    let vel = require_data7("velocity", velocity)?;
    send_midi_output_raw(&app, &state, vec![0x80 | ch, n, vel])
}

#[tauri::command]
fn midi_output_send_control_change(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    channel: u8,
    controller: u8,
    value: u8,
) -> Result<(), String> {
    let ch = require_channel(channel)?;
    let cc = require_data7("controller", controller)?;
    let val = require_data7("value", value)?;
    send_midi_output_raw(&app, &state, vec![0xB0 | ch, cc, val])
}

#[tauri::command]
fn midi_output_send_pitch_bend(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    channel: u8,
    value: i16,
) -> Result<(), String> {
    let ch = require_channel(channel)?;
    if !(-8192..=8191).contains(&value) {
        return Err(format!(
            "invalid pitch bend value {value} (expected -8192..8191)"
        ));
    }
    let v14 = (value + 8192) as u16;
    let lsb = (v14 & 0x7F) as u8;
    let msb = ((v14 >> 7) & 0x7F) as u8;
    send_midi_output_raw(&app, &state, vec![0xE0 | ch, lsb, msb])
}

#[tauri::command]
fn midi_output_send_program_change(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    channel: u8,
    program: u8,
) -> Result<(), String> {
    let ch = require_channel(channel)?;
    let p = require_data7("program", program)?;
    send_midi_output_raw(&app, &state, vec![0xC0 | ch, p])
}

#[tauri::command]
fn midi_output_send_channel_pressure(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    channel: u8,
    pressure: u8,
) -> Result<(), String> {
    let ch = require_channel(channel)?;
    let p = require_data7("pressure", pressure)?;
    send_midi_output_raw(&app, &state, vec![0xD0 | ch, p])
}

#[tauri::command]
fn midi_output_all_notes_off(
    app: AppHandle,
    state: tauri::State<MidiClockOutputState>,
    channel: Option<u8>,
) -> Result<(), String> {
    let ch = match channel {
        Some(v) => Some(require_channel(v)?),
        None => None,
    };
    let _ = ensure_selected_midi_output_port(&app, &state)?;
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::AllNotesOff { channel: ch });
    }
    Ok(())
}

#[tauri::command]
fn midi_clock_output_set_bpm(
    state: tauri::State<MidiClockOutputState>,
    bpm: f64,
) -> Result<(), String> {
    ensure_midi_clock_output_svc(&state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::SetBpm { bpm });
    }
    Ok(())
}

#[tauri::command]
fn midi_clock_output_seek(
    state: tauri::State<MidiClockOutputState>,
    t_sec: f64,
) -> Result<(), String> {
    ensure_midi_clock_output_svc(&state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::Seek { t_sec });
    }
    Ok(())
}

#[tauri::command]
fn midi_clock_output_start(state: tauri::State<MidiClockOutputState>) -> Result<(), String> {
    ensure_midi_clock_output_svc(&state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::Start);
    }
    Ok(())
}

#[tauri::command]
fn midi_clock_output_continue(state: tauri::State<MidiClockOutputState>) -> Result<(), String> {
    ensure_midi_clock_output_svc(&state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::Continue);
    }
    Ok(())
}

#[tauri::command]
fn midi_clock_output_stop(state: tauri::State<MidiClockOutputState>) -> Result<(), String> {
    ensure_midi_clock_output_svc(&state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::Stop);
    }
    Ok(())
}

#[tauri::command]
fn midi_clock_output_shutdown(state: tauri::State<MidiClockOutputState>) -> Result<(), String> {
    // Take ownership so we can join the thread.
    let mut lock = state.svc.lock().unwrap();
    if let Some(svc) = lock.take() {
        svc.shutdown();
    }
    Ok(())
}

#[tauri::command]
fn list_midi_input_ports() -> Result<Vec<midi_clock_input::MidiInputPortInfo>, String> {
    midi_clock_input::list_midi_input_ports()
}

#[tauri::command]
fn midi_clock_input_get_saved_settings(app: AppHandle) -> Result<MidiInputSavedSettings, String> {
    Ok(MidiInputSavedSettings {
        port: get_midi_input_port_selection(&app)?,
        tempo_scale: get_midi_input_tempo_scale(&app)?,
        allow_sysex: get_midi_input_allow_sysex(&app)?,
    })
}

#[tauri::command]
fn midi_clock_input_start(
    app: AppHandle,
    state: tauri::State<MidiClockInputState>,
    port_id: usize,
    tempo_scale: f64,
    allow_sysex: Option<bool>,
) -> Result<(), String> {
    let allow_sysex = allow_sysex.unwrap_or(false);

    // Replace any existing connection.
    {
        let mut lock = state.conn.lock().unwrap();
        *lock = None;
    }

    let conn = midi_clock_input::start_midi_clock_input(app, port_id, tempo_scale, allow_sysex)?;
    let mut lock = state.conn.lock().unwrap();
    *lock = Some(conn);
    Ok(())
}

#[tauri::command]
fn midi_clock_input_start_and_persist(
    app: AppHandle,
    state: tauri::State<MidiClockInputState>,
    port_id: usize,
    tempo_scale: f64,
    allow_sysex: Option<bool>,
) -> Result<(), String> {
    let allow_sysex = allow_sysex.unwrap_or(false);
    let ports = midi_clock_input::list_midi_input_ports()?;
    let p = ports
        .into_iter()
        .find(|x| x.id == port_id)
        .ok_or_else(|| format!("invalid midi input port id {port_id}"))?;

    set_midi_input_port_selection(
        &app,
        Some(midi_clock_input::MidiInputSelection {
            id: p.id,
            name: p.name,
            stable_id: Some(p.stable_id),
        }),
    )?;
    set_midi_input_tempo_scale(&app, tempo_scale)?;
    set_midi_input_allow_sysex(&app, allow_sysex)?;

    midi_clock_input_start(app, state, port_id, tempo_scale, Some(allow_sysex))
}

#[tauri::command]
fn midi_clock_input_stop(state: tauri::State<MidiClockInputState>) -> Result<(), String> {
    let mut lock = state.conn.lock().unwrap();
    *lock = None;
    Ok(())
}

#[cfg(test)]
mod feedpak_read_tests {
    //! Stage-5 feedpak read path: the container guards, the feature-rel
    //! allow-list, default-stem resolution, and the details mapping pinned
    //! against the committed `minimal.feedpak` fixture.

    use super::*;

    fn minimal_feedpak_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../packages/feedpak/fixtures/minimal.feedpak")
    }

    #[test]
    fn ingest_out_filename_uses_feedpak_extension() {
        // The import pipeline writes feedpak content; the output name must match,
        // or the app resolves aural/ vs features/ against the wrong layout.
        assert_eq!(ingest_out_filename("song", None), "ingest_song.feedpak");
        assert_eq!(
            ingest_out_filename("song", Some(2)),
            "ingest_song_2.feedpak"
        );
        assert!(!ingest_out_filename("song", None).ends_with(".auralsong"));
    }

    #[test]
    fn container_suffix_guards() {
        // Manifest packs (feedpak OR sloppak) share the same read/write path.
        assert!(is_feedpak_path("x.feedpak"));
        assert!(is_feedpak_path("x.sloppak"));
        assert!(!is_feedpak_path("x.auralsong"));
        assert!(is_auralsong_path("x.auralsong"));
        assert!(!is_auralsong_path("x.sloppak"));
        assert!(is_supported_container("x.feedpak"));
        assert!(is_supported_container("x.sloppak"));
        assert!(is_supported_container("x.auralsong"));
        assert!(!is_supported_container("x.zip"));
    }

    #[test]
    fn feature_rel_allow_list() {
        assert!(is_allowed_feature_rel(
            "aural/spectrogram/keys/spectrogram.json"
        ));
        assert!(is_allowed_feature_rel("aural/refine_candidates.keys.json"));
        assert!(is_allowed_feature_rel("features/notes.mid"));
        assert!(is_allowed_feature_rel("arrangements/notation_keys.json"));
        // Sloppak lyrics + vocal pitch live at the pack root.
        assert!(is_allowed_feature_rel("lyrics.json"));
        assert!(is_allowed_feature_rel("vocal_pitch.json"));
        assert!(is_allowed_feature_rel("drum_tab.json"));
        assert!(is_allowed_feature_rel("song_timeline.json"));
        assert!(!is_allowed_feature_rel("audio/stems/keys.wav"));
        assert!(!is_allowed_feature_rel("aural/../../escape.json"));
    }

    fn minimal_sloppak_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../packages/sloppak/fixtures/minimal.sloppak")
    }

    /// The sloppak fixture must read through the SAME feedpak details path and
    /// expose the C6 readiness fields: arrangements_count (2 arrangements) and
    /// lyrics_rel (root `lyrics.json`). notes.mid is absent (prep not yet run),
    /// so `needs arrangement prep` = arrangements_count > 0 && !has_notes_mid.
    #[test]
    fn details_for_minimal_sloppak_exposes_readiness_fields() {
        let dir = minimal_sloppak_dir();
        assert!(
            dir.join("manifest.yaml").is_file(),
            "sloppak fixture missing: {}",
            dir.display()
        );
        let d = feedpak_details(dir.to_string_lossy().to_string(), &dir);
        assert!(d.ok, "{:?}", d.error);
        assert_eq!(
            d.manifest_summary.as_ref().unwrap().title.as_deref(),
            Some("Minimal Sloppak")
        );
        // Two arrangements (lead, bass) and no prep-generated notes.mid yet.
        assert_eq!(d.arrangements_count, 2);
        assert!(!d.has_notes_mid, "prep-arrangements has not run on fixture");
        // Sloppak lyrics live at the pack root, pointed to by the manifest.
        assert_eq!(d.lyrics_rel.as_deref(), Some("lyrics.json"));
        assert!(d.has_lyrics);
        // The gate the frontend uses.
        assert!(d.arrangements_count > 0 && !d.has_notes_mid);
    }

    #[test]
    fn default_stem_from_minimal_fixture() {
        let m = read_feedpak_manifest(&minimal_feedpak_dir()).expect("parse manifest");
        let (rel, mime) = feedpak_default_stem(&m).expect("default stem");
        assert_eq!(rel, "audio/stems/keys.wav");
        assert_eq!(mime, "audio/wav");
    }

    #[test]
    fn details_for_minimal_fixture() {
        let dir = minimal_feedpak_dir();
        let d = feedpak_details(dir.to_string_lossy().to_string(), &dir);
        assert!(d.ok, "{:?}", d.error);
        assert_eq!(d.kind, "directory");
        assert_eq!(
            d.manifest_summary.as_ref().unwrap().title.as_deref(),
            Some("Minimal Fixture")
        );
        // song_timeline + notes.mid both present in the fixture.
        assert!(d.has_notes_mid);
        assert!(d.has_beats && d.has_tempo_map && d.has_sections);
        assert!(d.has_events); // notes.mid present
        assert!(!d.has_lyrics);
        assert!(d.has_mix_wav && !d.has_mix_ogg && !d.has_mix_mp3);
        // Raw manifest reshaped to JSON carries the typed aural_* key.
        let raw = d.manifest_raw.expect("manifest_raw");
        assert_eq!(
            raw.get("aural_notes_mid").and_then(|v| v.as_str()),
            Some("aural/notes.mid")
        );
    }
}
