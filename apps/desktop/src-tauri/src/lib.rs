#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use tauri::{AppHandle, Manager};

mod models;
mod midi_clock;
mod midi_clock_input;
mod midi_clock_service;
pub mod wav_mix;
pub mod audio_engine;
mod audio_decode;
mod native_audio;
pub mod ghwt;
pub mod stem_midi;
pub mod raw_song;

#[derive(Debug, Serialize, Deserialize, Default)]
struct Settings {
    #[serde(default)]
    songs_folder: Option<String>,

    #[serde(default)]
    visualizers_folder: Option<String>,

    // --- MIDI output clock ---
    #[serde(default)]
    midi_clock_output_port: Option<midi_clock::MidiOutputSelection>,

    // --- Import sources ---
    #[serde(default)]
    ghwt_data_root: Option<String>,

    #[serde(default)]
    ghwt_vgmstream_cli_path: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct GhwtSettings {
    pub data_root: Option<String>,
    pub vgmstream_cli_path: Option<String>,
}

#[derive(Debug, Serialize)]
struct SongsFolderPaths {
    config_dir: String,
    data_dir: String,
    default_songs_folder: String,
    default_visualizers_folder: String,
    settings_path: String,
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct ManifestSummary {
    pub schema_version: Option<String>,
    pub song_id: Option<String>,
    pub title: Option<String>,
    pub artist: Option<String>,
    pub duration_sec: Option<f64>,
}

#[derive(Debug, Serialize)]
pub struct SongPackDetails {
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

    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct SongPackScanEntry {
    pub container_path: String,
    pub kind: String,
    pub ok: bool,
    pub manifest: Option<ManifestSummary>,
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
pub struct LoadedSongPackAudioInfo {
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

// -----------------
// Native audio (Phase 1)
// -----------------

#[derive(Default)]
struct NativeAudioState {
    inner: native_audio::NativeAudioEngineState,
}

fn get_paths(app: &AppHandle) -> Result<SongsFolderPaths, String> {
    let config_dir = app
        .path()
        .app_config_dir()
        .map_err(|e| format!("app_config_dir: {e}"))?;
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir: {e}"))?;

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
    let json = serde_json::to_string_pretty(settings).map_err(|e| format!("serialize settings: {e}"))?;

    fs::write(&tmp_path, json).map_err(|e| format!("write tmp settings: {e}"))?;
    fs::rename(&tmp_path, &paths.settings_path).map_err(|e| format!("rename settings: {e}"))?;
    Ok(())
}

fn parse_manifest_json(raw: &str) -> Result<ManifestSummary, String> {
    let v: serde_json::Value = serde_json::from_str(raw).map_err(|e| format!("invalid JSON: {e}"))?;

    // Keep it flexible: pull out common fields if present.
    Ok(ManifestSummary {
        schema_version: v.get("schema_version").and_then(|x| x.as_str()).map(|s| s.to_string()),
        song_id: v.get("song_id").and_then(|x| x.as_str()).map(|s| s.to_string()),
        title: v.get("title").and_then(|x| x.as_str()).map(|s| s.to_string()),
        artist: v.get("artist").and_then(|x| x.as_str()).map(|s| s.to_string()),
        duration_sec: v.get("duration_sec").and_then(|x| x.as_f64()),
    })
}

fn read_dir_manifest(songpack_dir: &Path) -> Result<ManifestSummary, String> {
    let manifest_path = songpack_dir.join("manifest.json");
    let raw = fs::read_to_string(&manifest_path)
        .map_err(|e| format!("read {}: {e}", manifest_path.display()))?;
    parse_manifest_json(&raw)
}

fn read_zip_manifest(songpack_zip: &Path) -> Result<ManifestSummary, String> {
    let f = fs::File::open(songpack_zip).map_err(|e| format!("open {}: {e}", songpack_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    let mut file = archive
        .by_name("manifest.json")
        .map_err(|e| format!("zip missing manifest.json: {e}"))?;
    let mut raw = String::new();
    file.read_to_string(&mut raw).map_err(|e| format!("zip read manifest.json: {e}"))?;
    parse_manifest_json(&raw)
}

fn parse_manifest_raw(raw: &str) -> Result<serde_json::Value, String> {
    serde_json::from_str(raw).map_err(|e| format!("invalid JSON: {e}"))
}

fn read_dir_manifest_raw(songpack_dir: &Path) -> Result<serde_json::Value, String> {
    let manifest_path = songpack_dir.join("manifest.json");
    let raw = fs::read_to_string(&manifest_path)
        .map_err(|e| format!("read {}: {e}", manifest_path.display()))?;
    parse_manifest_raw(&raw)
}

fn read_zip_manifest_raw(songpack_zip: &Path) -> Result<serde_json::Value, String> {
    let f = fs::File::open(songpack_zip).map_err(|e| format!("open {}: {e}", songpack_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    let mut file = archive
        .by_name("manifest.json")
        .map_err(|e| format!("zip missing manifest.json: {e}"))?;
    let mut raw = String::new();
    file.read_to_string(&mut raw).map_err(|e| format!("zip read manifest.json: {e}"))?;
    parse_manifest_raw(&raw)
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

fn zip_has_file(songpack_zip: &Path, rel: &str) -> Result<bool, String> {
    let f = fs::File::open(songpack_zip).map_err(|e| format!("open {}: {e}", songpack_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    // Ensure the ZipFile temporary is dropped before `archive` is dropped.
    let exists = archive.by_name(rel).is_ok();
    Ok(exists)
}

fn zip_list_charts(songpack_zip: &Path) -> Result<Vec<String>, String> {
    let f = fs::File::open(songpack_zip).map_err(|e| format!("open {}: {e}", songpack_zip.display()))?;
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

fn read_dir_audio(songpack_dir: &Path, rel: &str) -> Result<Vec<u8>, String> {
    let p = songpack_dir.join(rel);
    fs::read(&p).map_err(|e| format!("read {}: {e}", p.display()))
}

fn read_zip_audio(songpack_zip: &Path, rel: &str) -> Result<Vec<u8>, String> {
    let f = fs::File::open(songpack_zip).map_err(|e| format!("open {}: {e}", songpack_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    let mut file = archive
        .by_name(rel)
        .map_err(|e| format!("zip missing {rel}: {e}"))?;

    let mut buf: Vec<u8> = vec![];
    file.read_to_end(&mut buf)
        .map_err(|e| format!("zip read {rel}: {e}"))?;
    Ok(buf)
}

fn read_dir_text(songpack_dir: &Path, rel: &str) -> Result<String, String> {
    let p = songpack_dir.join(rel);
    fs::read_to_string(&p).map_err(|e| format!("read {}: {e}", p.display()))
}

fn read_zip_text(songpack_zip: &Path, rel: &str) -> Result<String, String> {
    let f = fs::File::open(songpack_zip).map_err(|e| format!("open {}: {e}", songpack_zip.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;
    let mut file = archive.by_name(rel).map_err(|e| format!("zip missing {rel}: {e}"))?;
    let mut raw = String::new();
    file.read_to_string(&mut raw)
        .map_err(|e| format!("zip read {rel}: {e}"))?;
    Ok(raw)
}

fn unzip_songpack_to_dir(zip_path: &Path, dst_dir: &Path) -> Result<(), String> {
    let f = fs::File::open(zip_path).map_err(|e| format!("open {}: {e}", zip_path.display()))?;
    let mut archive = zip::ZipArchive::new(f).map_err(|e| format!("zip open: {e}"))?;

    fs::create_dir_all(dst_dir).map_err(|e| format!("mkdir {}: {e}", dst_dir.display()))?;

    for i in 0..archive.len() {
        let mut file = archive.by_index(i).map_err(|e| format!("zip index {i}: {e}"))?;
        let name = file.name().to_string();

        // Path traversal prevention.
        let rel = Path::new(&name);
        if rel.components().any(|c| matches!(c, std::path::Component::ParentDir)) {
            return Err("zip path traversal detected".to_string());
        }

        let out_path = dst_dir.join(rel);
        if file.is_dir() {
            fs::create_dir_all(&out_path).map_err(|e| format!("mkdir {}: {e}", out_path.display()))?;
            continue;
        }

        if let Some(parent) = out_path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("mkdir {}: {e}", parent.display()))?;
        }

        let mut out_f = fs::File::create(&out_path).map_err(|e| format!("create {}: {e}", out_path.display()))?;
        std::io::copy(&mut file, &mut out_f).map_err(|e| format!("write {}: {e}", out_path.display()))?;
    }

    Ok(())
}

#[tauri::command]
fn convert_songpack_to_directory(app: AppHandle, container_path: String) -> Result<String, String> {
    let p = PathBuf::from(&container_path);
    if !container_path.ends_with(".songpack") {
        return Err("path does not end with .songpack".to_string());
    }

    // Already a directory.
    if p.is_dir() {
        return Ok(container_path);
    }

    let songs_folder = PathBuf::from(get_songs_folder(app.clone())?);
    let name = p
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or_else(|| "invalid songpack filename".to_string())?;

    let base = songs_folder.join(name);
    let dst = if !base.exists() {
        base
    } else {
        // Avoid clobber: create a sibling with a deterministic suffix.
        let stem = p.file_stem().and_then(|s| s.to_str()).unwrap_or("song");
        let mut idx = 1;
        loop {
            let candidate = songs_folder.join(format!("{}_dir{}.songpack", stem, idx));
            if !candidate.exists() {
                break candidate;
            }
            idx += 1;
            if idx > 999 {
                return Err("failed to find available directory name".to_string());
            }
        }
    };

    unzip_songpack_to_dir(&p, &dst)?;
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
fn set_visualizers_folder_override(app: AppHandle, visualizers_folder: String) -> Result<(), String> {
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
    save_settings(&paths, &settings)
}

#[tauri::command]
fn clear_songs_folder_override(app: AppHandle) -> Result<(), String> {
    let paths = get_paths(&app)?;
    let mut settings = load_settings(&paths);
    settings.songs_folder = None;
    save_settings(&paths, &settings)
}

// -----------------
// Native audio (Phase 1)
// -----------------

#[tauri::command]
fn native_audio_list_output_devices() -> Result<Vec<native_audio::NativeAudioDeviceInfo>, String> {
    native_audio::list_output_devices()
}

#[tauri::command]
fn native_audio_init(state: tauri::State<NativeAudioState>, sample_rate_hz: u32, channels: u16) -> Result<(), String> {
    let mut lock = state.inner.engine.lock().unwrap();
    *lock = Some(native_audio::NativeAudioHandle::new(sample_rate_hz, channels)?);
    Ok(())
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
fn native_audio_load_wav_bytes(state: tauri::State<NativeAudioState>, wav_bytes: Vec<u8>) -> Result<(), String> {
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

    // Ensure engine exists and matches SR/channels; otherwise recreate.
    {
        let mut lock = state.inner.engine.lock().unwrap();
        let needs_reinit = match lock.as_ref() {
            Some(e) => e.sample_rate_hz != decoded.sample_rate_hz || e.channels != decoded.channels,
            None => true,
        };
        if needs_reinit {
            if let Some(old) = lock.take() {
                old.shutdown();
            }
            *lock = Some(native_audio::NativeAudioHandle::new(
                decoded.sample_rate_hz,
                decoded.channels,
            )?);
        }
    }

    with_native_engine(&state, |e| e.load_pcm16(decoded.sample_rate_hz, decoded.channels, decoded.data))
}

#[tauri::command]
fn native_audio_play(state: tauri::State<NativeAudioState>) -> Result<(), String> {
    with_native_engine(&state, |e| {
        e.play();
        Ok(())
    })
}

#[tauri::command]
fn native_audio_pause(state: tauri::State<NativeAudioState>) -> Result<(), String> {
    with_native_engine(&state, |e| {
        e.pause();
        Ok(())
    })
}

#[tauri::command]
fn native_audio_stop(state: tauri::State<NativeAudioState>) -> Result<(), String> {
    with_native_engine(&state, |e| {
        e.stop();
        Ok(())
    })
}

#[tauri::command]
fn native_audio_seek(state: tauri::State<NativeAudioState>, t_sec: f64) -> Result<(), String> {
    with_native_engine(&state, |e| {
        e.seek_seconds(t_sec);
        Ok(())
    })
}

#[tauri::command]
fn native_audio_set_loop(state: tauri::State<NativeAudioState>, t0: Option<f64>, t1: Option<f64>) -> Result<(), String> {
    with_native_engine(&state, |e| e.set_loop_seconds(t0, t1))
}

#[tauri::command]
fn native_audio_set_playback_rate(state: tauri::State<NativeAudioState>, rate: f64) -> Result<(), String> {
    with_native_engine(&state, |e| {
        e.set_playback_rate(rate);
        Ok(())
    })
}

#[tauri::command]
fn native_audio_get_state(state: tauri::State<NativeAudioState>) -> Result<native_audio::NativeAudioState, String> {
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
// GHWT-DE importer (MVP)
// -----------------

#[tauri::command]
fn ghwt_preflight(app: AppHandle, data_root: Option<String>, vgmstream_cli_path: Option<String>) -> Result<ghwt::GhwtPreflight, String> {
    let root = data_root.unwrap_or(get_ghwt_data_root(&app)?);
    let vgm = if vgmstream_cli_path.is_some() {
        vgmstream_cli_path
    } else {
        get_ghwt_vgmstream_path(&app)?
    };
    Ok(ghwt::preflight(Path::new(&root), vgm))
}

#[tauri::command]
fn get_ghwt_settings(app: AppHandle) -> Result<GhwtSettings, String> {
    let paths = get_paths(&app)?;
    let settings = load_settings(&paths);
    Ok(GhwtSettings {
        data_root: settings.ghwt_data_root,
        vgmstream_cli_path: settings.ghwt_vgmstream_cli_path,
    })
}

#[tauri::command]
fn set_ghwt_settings(app: AppHandle, data_root: Option<String>, vgmstream_cli_path: Option<String>) -> Result<(), String> {
    let paths = get_paths(&app)?;
    let mut settings = load_settings(&paths);
    settings.ghwt_data_root = data_root;
    settings.ghwt_vgmstream_cli_path = vgmstream_cli_path;
    save_settings(&paths, &settings)
}

fn get_ghwt_data_root(app: &AppHandle) -> Result<String, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    settings
        .ghwt_data_root
        .ok_or_else(|| "GHWT data root not configured".to_string())
}

fn get_ghwt_vgmstream_path(app: &AppHandle) -> Result<Option<String>, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    Ok(settings.ghwt_vgmstream_cli_path)
}

#[tauri::command]
fn ghwt_scan_dlc(app: AppHandle, data_root: Option<String>) -> Result<Vec<ghwt::GhwtSongEntry>, String> {
    let root = data_root.unwrap_or(get_ghwt_data_root(&app)?);
    ghwt::scan_dlc(Path::new(&root))
}

#[tauri::command]
fn ghwt_import_preview(app: AppHandle, checksum: String, data_root: Option<String>, vgmstream_cli_path: Option<String>) -> Result<ghwt::GhwtImportResult, String> {
    let root = data_root.unwrap_or(get_ghwt_data_root(&app)?);
    let vgm = if vgmstream_cli_path.is_some() {
        vgmstream_cli_path
    } else {
        get_ghwt_vgmstream_path(&app)?
    };
    ghwt::import_preview_to_songpack(&app, Path::new(&root), &checksum, vgm)
}

#[tauri::command]
fn ghwt_import_all(app: AppHandle, data_root: Option<String>, vgmstream_cli_path: Option<String>) -> Result<Vec<ghwt::GhwtImportAllResult>, String> {
    let root = data_root.unwrap_or(get_ghwt_data_root(&app)?);
    let vgm = if vgmstream_cli_path.is_some() {
        vgmstream_cli_path
    } else {
        get_ghwt_vgmstream_path(&app)?
    };
    let songs_folder = PathBuf::from(get_songs_folder(app.clone())?);
    ghwt::import_all_to_folder(Some(&app), Path::new(&root), &songs_folder, vgm)
}

// -----------------
// Stem+MIDI SongPack creator
// -----------------

#[tauri::command]
fn stem_midi_create_songpack(app: AppHandle, req: stem_midi::StemMidiCreateRequest) -> Result<stem_midi::StemMidiCreateResult, String> {
    let songs_folder = PathBuf::from(get_songs_folder(app.clone())?);
    stem_midi::create_songpack(req, &songs_folder)
}

#[tauri::command]
fn import_raw_song_folder(
    app: AppHandle,
    req: raw_song::ImportRawSongFolderRequest,
) -> Result<raw_song::ImportRawSongFolderResult, String> {
    let songs_folder = PathBuf::from(get_songs_folder(app.clone())?);
    raw_song::import_raw_song_folder(req, &songs_folder)
}

#[tauri::command]
fn scan_songpacks(app: AppHandle) -> Result<Vec<SongPackScanEntry>, String> {
    let folder = get_songs_folder(app.clone())?;
    let root = PathBuf::from(folder);

    // Ensure the songs folder exists on first run.
    if let Err(e) = fs::create_dir_all(&root) {
        return Ok(vec![SongPackScanEntry {
            container_path: root.to_string_lossy().to_string(),
            kind: "songs_folder".to_string(),
            ok: false,
            manifest: None,
            error: Some(format!("cannot create songs folder: {e}")),
        }]);
    }

    let mut out: Vec<SongPackScanEntry> = vec![];

    let entries = match fs::read_dir(&root) {
        Ok(e) => e,
        Err(e) => {
            return Ok(vec![SongPackScanEntry {
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
        let is_songpack = file_name.ends_with(".songpack");
        if !is_songpack {
            continue;
        }

        if p.is_dir() {
            match read_dir_manifest(&p) {
                Ok(m) => out.push(SongPackScanEntry {
                    container_path: p.to_string_lossy().to_string(),
                    kind: "directory".to_string(),
                    ok: true,
                    manifest: Some(m),
                    error: None,
                }),
                Err(e) => out.push(SongPackScanEntry {
                    container_path: p.to_string_lossy().to_string(),
                    kind: "directory".to_string(),
                    ok: false,
                    manifest: None,
                    error: Some(e),
                }),
            }
        } else {
            match read_zip_manifest(&p) {
                Ok(m) => out.push(SongPackScanEntry {
                    container_path: p.to_string_lossy().to_string(),
                    kind: "zip".to_string(),
                    ok: true,
                    manifest: Some(m),
                    error: None,
                }),
                Err(e) => out.push(SongPackScanEntry {
                    container_path: p.to_string_lossy().to_string(),
                    kind: "zip".to_string(),
                    ok: false,
                    manifest: None,
                    error: Some(e),
                }),
            }
        }
    }

    Ok(out)
}

#[tauri::command]
fn read_songpack_audio(container_path: String) -> Result<AudioBlob, String> {
    let p = PathBuf::from(&container_path);

    if !container_path.ends_with(".songpack") {
        return Err("path does not end with .songpack".to_string());
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
    } else {
        if zip_has_file(&p, "audio/mix.ogg").unwrap_or(false) {
            ("audio/mix.ogg", "audio/ogg")
        } else if zip_has_file(&p, "audio/mix.mp3").unwrap_or(false) {
            ("audio/mix.mp3", "audio/mpeg")
        } else if zip_has_file(&p, "audio/mix.wav").unwrap_or(false) {
            ("audio/mix.wav", "audio/wav")
        } else {
            return Err("no audio/mix.ogg, audio/mix.mp3, or audio/mix.wav found".to_string());
        }
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
fn native_audio_load_songpack_audio(
    state: tauri::State<NativeAudioState>,
    container_path: String,
) -> Result<LoadedSongPackAudioInfo, String> {
    let p = PathBuf::from(&container_path);

    if !container_path.ends_with(".songpack") {
        return Err("path does not end with .songpack".to_string());
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
    } else {
        if zip_has_file(&p, "audio/mix.ogg").unwrap_or(false) {
            ("audio/mix.ogg", "audio/ogg")
        } else if zip_has_file(&p, "audio/mix.mp3").unwrap_or(false) {
            ("audio/mix.mp3", "audio/mpeg")
        } else if zip_has_file(&p, "audio/mix.wav").unwrap_or(false) {
            ("audio/mix.wav", "audio/wav")
        } else {
            return Err("no audio/mix.ogg, audio/mix.mp3, or audio/mix.wav found".to_string());
        }
    };

    // Read audio bytes on the Rust side (no IPC transfer).
    let bytes = if p.is_dir() {
        read_dir_audio(&p, rel)?
    } else {
        read_zip_audio(&p, rel)?
    };

    // Decode + load into engine (this may reinit engine to match SR/channels).
    let decoded = audio_decode::decode_to_pcm16(&bytes, mime)?;
    let duration_sec = if decoded.sample_rate_hz > 0 && decoded.channels > 0 {
        // interleaved i16 samples
        (decoded.data.len() as f64) / (decoded.sample_rate_hz as f64) / (decoded.channels as f64)
    } else {
        0.0
    };

    // Ensure engine exists and matches SR/channels; otherwise recreate.
    {
        let mut lock = state.inner.engine.lock().unwrap();
        let needs_reinit = match lock.as_ref() {
            Some(e) => e.sample_rate_hz != decoded.sample_rate_hz || e.channels != decoded.channels,
            None => true,
        };
        if needs_reinit {
            if let Some(old) = lock.take() {
                old.shutdown();
            }
            *lock = Some(native_audio::NativeAudioHandle::new(
                decoded.sample_rate_hz,
                decoded.channels,
            )?);
        }
    }

    with_native_engine(&state, |e| e.load_pcm16(decoded.sample_rate_hz, decoded.channels, decoded.data))?;

    Ok(LoadedSongPackAudioInfo {
        mime: mime.to_string(),
        duration_sec,
    })
}

#[tauri::command]
fn read_songpack_json(container_path: String, rel_path: String) -> Result<serde_json::Value, String> {
    let p = PathBuf::from(&container_path);
    if !container_path.ends_with(".songpack") {
        return Err("path does not end with .songpack".to_string());
    }
    if !rel_path.starts_with("features/") {
        return Err("only features/* json is allowed".to_string());
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
fn read_songpack_mid(container_path: String, rel_path: String) -> Result<MidiBlob, String> {
    let p = PathBuf::from(&container_path);
    if !container_path.ends_with(".songpack") {
        return Err("path does not end with .songpack".to_string());
    }
    if !rel_path.starts_with("features/") {
        return Err("only features/* is allowed".to_string());
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

#[tauri::command]
fn read_songpack_charts(container_path: String) -> Result<serde_json::Value, String> {
    let p = PathBuf::from(&container_path);
    if !container_path.ends_with(".songpack") {
        return Err("path does not end with .songpack".to_string());
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
        let v: serde_json::Value = serde_json::from_str(&raw)
            .map_err(|e| format!("invalid JSON in {}: {e}", rel))?;
        out.insert(rel, v);
    }

    Ok(serde_json::Value::Object(out))
}

#[tauri::command]
fn write_songpack_lyrics_json(container_path: String, lyrics_json: serde_json::Value) -> Result<(), String> {
    let p = PathBuf::from(&container_path);
    if !container_path.ends_with(".songpack") {
        return Err("path does not end with .songpack".to_string());
    }
    if !p.is_dir() {
        return Err("writing features is only supported for directory SongPacks (not .songpack zip files)".to_string());
    }

    let features_dir = p.join("features");
    fs::create_dir_all(&features_dir).map_err(|e| format!("mkdir {}: {e}", features_dir.display()))?;
    let out_path = features_dir.join("lyrics.json");

    let raw = serde_json::to_string_pretty(&lyrics_json).map_err(|e| format!("serialize lyrics json: {e}"))?;
    fs::write(&out_path, raw).map_err(|e| format!("write {}: {e}", out_path.display()))?;
    Ok(())
}

#[tauri::command]
fn get_songpack_details(container_path: String) -> Result<SongPackDetails, String> {
    let p = PathBuf::from(&container_path);

    if !container_path.ends_with(".songpack") {
        return Ok(SongPackDetails {
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
            error: Some("path does not end with .songpack".to_string()),
        });
    }

    if p.is_dir() {
        // Directory SongPack
        let manifest_raw = match read_dir_manifest_raw(&p) {
            Ok(v) => Some(v),
            Err(e) => {
                return Ok(SongPackDetails {
                    container_path,
                    kind: "directory".to_string(),
                    ok: false,
                    manifest_summary: None,
                    manifest_raw: None,
                    has_beats: dir_has_file(&p, "features/beats.json"),
                    has_tempo_map: dir_has_file(&p, "features/tempo_map.json"),
                    has_sections: dir_has_file(&p, "features/sections.json"),
                    has_events: dir_has_file(&p, "features/events.json"),
                    has_lyrics: dir_has_file(&p, "features/lyrics.json"),
                    has_notes_mid: dir_has_file(&p, "features/notes.mid"),
                    has_mix_mp3: dir_has_file(&p, "audio/mix.mp3"),
                    has_mix_ogg: dir_has_file(&p, "audio/mix.ogg"),
                    has_mix_wav: dir_has_file(&p, "audio/mix.wav"),
                    charts: dir_list_charts(&p),
                    error: Some(e),
                });
            }
        };

        let manifest_summary = match fs::read_to_string(p.join("manifest.json")) {
            Ok(raw) => parse_manifest_json(&raw).ok(),
            Err(_) => None,
        };

        Ok(SongPackDetails {
            container_path,
            kind: "directory".to_string(),
            ok: true,
            manifest_summary,
            manifest_raw,
            has_beats: dir_has_file(&p, "features/beats.json"),
            has_tempo_map: dir_has_file(&p, "features/tempo_map.json"),
            has_sections: dir_has_file(&p, "features/sections.json"),
            has_events: dir_has_file(&p, "features/events.json"),
            has_lyrics: dir_has_file(&p, "features/lyrics.json"),
            has_notes_mid: dir_has_file(&p, "features/notes.mid"),
            has_mix_mp3: dir_has_file(&p, "audio/mix.mp3"),
            has_mix_ogg: dir_has_file(&p, "audio/mix.ogg"),
            has_mix_wav: dir_has_file(&p, "audio/mix.wav"),
            charts: dir_list_charts(&p),
            error: None,
        })
    } else {
        // Zip SongPack
        let manifest_raw = match read_zip_manifest_raw(&p) {
            Ok(v) => Some(v),
            Err(e) => {
                return Ok(SongPackDetails {
                    container_path,
                    kind: "zip".to_string(),
                    ok: false,
                    manifest_summary: None,
                    manifest_raw: None,
                    has_beats: zip_has_file(&p, "features/beats.json").unwrap_or(false),
                    has_tempo_map: zip_has_file(&p, "features/tempo_map.json").unwrap_or(false),
                    has_sections: zip_has_file(&p, "features/sections.json").unwrap_or(false),
                    has_events: zip_has_file(&p, "features/events.json").unwrap_or(false),
                    has_lyrics: zip_has_file(&p, "features/lyrics.json").unwrap_or(false),
                    has_notes_mid: zip_has_file(&p, "features/notes.mid").unwrap_or(false),
                    has_mix_mp3: zip_has_file(&p, "audio/mix.mp3").unwrap_or(false),
                    has_mix_ogg: zip_has_file(&p, "audio/mix.ogg").unwrap_or(false),
                    has_mix_wav: zip_has_file(&p, "audio/mix.wav").unwrap_or(false),
                    charts: zip_list_charts(&p).unwrap_or_default(),
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

        Ok(SongPackDetails {
            container_path,
            kind: "zip".to_string(),
            ok: true,
            manifest_summary,
            manifest_raw,
            has_beats: zip_has_file(&p, "features/beats.json").unwrap_or(false),
            has_tempo_map: zip_has_file(&p, "features/tempo_map.json").unwrap_or(false),
            has_sections: zip_has_file(&p, "features/sections.json").unwrap_or(false),
            has_events: zip_has_file(&p, "features/events.json").unwrap_or(false),
            has_lyrics: zip_has_file(&p, "features/lyrics.json").unwrap_or(false),
            has_notes_mid: zip_has_file(&p, "features/notes.mid").unwrap_or(false),
            has_mix_mp3: zip_has_file(&p, "audio/mix.mp3").unwrap_or(false),
            has_mix_ogg: zip_has_file(&p, "audio/mix.ogg").unwrap_or(false),
            has_mix_wav: zip_has_file(&p, "audio/mix.wav").unwrap_or(false),
            charts: zip_list_charts(&p).unwrap_or_default(),
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

fn scan_visualizers_dir(root: &Path, ensure_exists: bool) -> Result<Vec<VisualizerScanEntry>, String> {
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
                let entry_rel = m.entry.clone().unwrap_or_else(|| "dist/index.js".to_string());
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
fn install_modelpack_zip_bytes(app: AppHandle, req: models::InstallModelPackZipRequest) -> Result<(), String> {
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
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // Restore persisted MIDI clock output selection (best-effort).
            let handle = app.handle();
            if let Ok(Some(sel)) = get_midi_clock_output_port_selection(&handle) {
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

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            ping,
            get_songs_folder_paths,
            get_songs_folder,
            set_songs_folder_override,
            clear_songs_folder_override,
            // native audio
            native_audio_list_output_devices,
            native_audio_init,
            native_audio_load_wav_bytes,
            native_audio_load_audio_bytes,
            native_audio_load_songpack_audio,
            native_audio_play,
            native_audio_pause,
            native_audio_stop,
            native_audio_seek,
            native_audio_set_loop,
            native_audio_set_playback_rate,
            native_audio_get_state,
            native_audio_shutdown,
            // ghwt
            get_ghwt_settings,
            set_ghwt_settings,
            ghwt_preflight,
            ghwt_scan_dlc,
            ghwt_import_preview,
            ghwt_import_all,
            // stem+midi
            stem_midi_create_songpack,
            import_raw_song_folder,
            scan_songpacks,
            get_songpack_details,
            read_songpack_audio,
            read_songpack_json,
            read_songpack_mid,
            read_songpack_charts,
            write_songpack_lyrics_json,
            read_text_file,
            convert_songpack_to_directory,
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
            list_midi_input_ports,
            midi_clock_input_start,
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

fn get_midi_clock_output_port_selection(app: &AppHandle) -> Result<Option<midi_clock::MidiOutputSelection>, String> {
    let paths = get_paths(app)?;
    let settings = load_settings(&paths);
    Ok(settings.midi_clock_output_port)
}

fn set_midi_clock_output_port_selection(app: &AppHandle, sel: Option<midi_clock::MidiOutputSelection>) -> Result<(), String> {
    let paths = get_paths(app)?;
    let mut settings = load_settings(&paths);
    settings.midi_clock_output_port = sel;
    save_settings(&paths, &settings)
}

#[tauri::command]
fn midi_clock_output_get_saved_port(app: AppHandle) -> Result<Option<midi_clock::MidiOutputSelection>, String> {
    get_midi_clock_output_port_selection(&app)
}

#[tauri::command]
fn midi_clock_output_select_port(state: tauri::State<MidiClockOutputState>, port_id: usize) -> Result<(), String> {
    ensure_midi_clock_output_svc(&state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::SelectPort { port_id });
    }
    Ok(())
}

#[tauri::command]
fn midi_clock_output_select_port_and_persist(app: AppHandle, state: tauri::State<MidiClockOutputState>, port_id: usize) -> Result<(), String> {
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

#[tauri::command]
fn midi_clock_output_set_bpm(state: tauri::State<MidiClockOutputState>, bpm: f64) -> Result<(), String> {
    ensure_midi_clock_output_svc(&state);
    if let Some(svc) = state.svc.lock().unwrap().as_ref() {
        svc.send(midi_clock_service::MidiClockCommand::SetBpm { bpm });
    }
    Ok(())
}

#[tauri::command]
fn midi_clock_output_seek(state: tauri::State<MidiClockOutputState>, t_sec: f64) -> Result<(), String> {
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
fn midi_clock_input_start(
    app: AppHandle,
    state: tauri::State<MidiClockInputState>,
    port_id: usize,
    tempo_scale: f64,
) -> Result<(), String> {
    // Replace any existing connection.
    {
        let mut lock = state.conn.lock().unwrap();
        *lock = None;
    }

    let conn = midi_clock_input::start_midi_clock_input(app, port_id, tempo_scale)?;
    let mut lock = state.conn.lock().unwrap();
    *lock = Some(conn);
    Ok(())
}

#[tauri::command]
fn midi_clock_input_stop(state: tauri::State<MidiClockInputState>) -> Result<(), String> {
    let mut lock = state.conn.lock().unwrap();
    *lock = None;
    Ok(())
}
