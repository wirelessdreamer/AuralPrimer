use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tauri::{AppHandle, Emitter};

use crate::wav_mix::{mix_wavs, read_wav_pcm16, write_wav_pcm16, WavPcm16};

#[derive(Debug, Serialize, Clone)]
pub struct GhwtSongEntry {
    pub checksum: String,
    pub title: String,
    pub artist: String,
    pub year: Option<i32>,

    /// Absolute path to DLC folder containing song.ini (e.g. .../DLC13)
    pub dlc_dir: String,
    /// Absolute path to preview audio bank (e.g. .../Content/MUSIC/DLC13_preview.fsb.xen)
    pub preview_fsb_path: String,

    /// Absolute paths to full-audio stems if present (usually 3)
    pub stem_fsb_paths: Vec<String>,
    /// Absolute path to pak (often contains charts/metadata)
    pub pak_path: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
pub struct GhwtImportResult {
    pub songpack_path: String,
    pub used: String,
}

#[derive(Debug, Serialize, Clone)]
pub struct GhwtImportAllResult {
    pub ok: bool,
    pub checksum: String,
    pub songpack_path: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
pub struct GhwtPreflight {
    pub dlc_ok: bool,
    pub vgmstream_ok: bool,
    pub data_root: String,
    pub dlc_root: String,
    pub vgmstream_resolved: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
pub struct ImportProgressEvent {
    pub song: String,
    pub r#type: String,
    pub id: String,
    pub progress: f64,
    pub message: Option<String>,
    pub artifact: Option<String>,
}

fn emit(app: Option<&AppHandle>, ev: ImportProgressEvent) {
    if let Some(app) = app {
        let _ = app.emit("ghwt_import_progress", ev);
    }
}

#[derive(Debug, Default, Clone)]
struct SongIniFields {
    checksum: Option<String>,
    title: Option<String>,
    artist: Option<String>,
    year: Option<i32>,
}

fn parse_song_ini(raw: &str) -> SongIniFields {
    // Minimal ini parser, tuned to GHWT-DE song.ini patterns.
    // We only care about keys under [SongInfo], but we tolerate being sloppy.
    let mut section = "";
    let mut out = SongIniFields::default();

    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with(';') || line.starts_with('#') {
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            section = &line[1..line.len() - 1];
            continue;
        }

        let Some((k, v)) = line.split_once('=') else {
            continue;
        };
        let key = k.trim();
        let val = v.trim();

        if section.eq_ignore_ascii_case("SongInfo") {
            if key.eq_ignore_ascii_case("Checksum") {
                out.checksum = Some(val.to_string());
            } else if key.eq_ignore_ascii_case("Title") {
                out.title = Some(val.to_string());
            } else if key.eq_ignore_ascii_case("Artist") {
                out.artist = Some(val.to_string());
            } else if key.eq_ignore_ascii_case("Year") {
                out.year = val.parse::<i32>().ok();
            }
        }
    }

    out
}

fn ghwt_dlc_root_from_data_root(data_root: &Path) -> PathBuf {
    data_root
        .join("MODS")
        .join("Guitar Hero_ World Tour Downloadable Content")
}

pub fn preflight(data_root: &Path, vgmstream_path: Option<String>) -> GhwtPreflight {
    let dlc_root = ghwt_dlc_root_from_data_root(data_root);
    if !data_root.is_dir() {
        return GhwtPreflight {
            dlc_ok: false,
            vgmstream_ok: false,
            data_root: data_root.to_string_lossy().to_string(),
            dlc_root: dlc_root.to_string_lossy().to_string(),
            vgmstream_resolved: None,
            error: Some("DATA root does not exist or is not a directory".to_string()),
        };
    }
    if !dlc_root.is_dir() {
        return GhwtPreflight {
            dlc_ok: false,
            vgmstream_ok: false,
            data_root: data_root.to_string_lossy().to_string(),
            dlc_root: dlc_root.to_string_lossy().to_string(),
            vgmstream_resolved: None,
            error: Some(format!(
                "DLC root not found (expected): {}",
                dlc_root.to_string_lossy()
            )),
        };
    }

    // Best-effort vgmstream check: try running it. This is OS-dependent but good enough.
    let exe = match vgmstream_path.clone() {
        Some(p) => PathBuf::from(p),
        None => PathBuf::from("vgmstream-cli"),
    };

    let mut cmd = Command::new(&exe);
    cmd.arg("-h");
    match cmd.output() {
        Ok(out) => {
            // vgmstream uses exit code 0 for help; accept any stdout/stderr.
            let ok = out.status.success();
            GhwtPreflight {
                dlc_ok: true,
                vgmstream_ok: ok,
                data_root: data_root.to_string_lossy().to_string(),
                dlc_root: dlc_root.to_string_lossy().to_string(),
                vgmstream_resolved: Some(exe.to_string_lossy().to_string()),
                error: if ok {
                    None
                } else {
                    Some("vgmstream-cli returned non-zero for -h".to_string())
                },
            }
        }
        Err(e) => {
            let msg = if e.kind() == std::io::ErrorKind::NotFound {
                "vgmstream-cli not found. Install vgmstream and ensure vgmstream-cli is on PATH (or set an explicit path)."
                    .to_string()
            } else {
                format!("failed to run vgmstream-cli: {e}")
            };
            GhwtPreflight {
                dlc_ok: true,
                vgmstream_ok: false,
                data_root: data_root.to_string_lossy().to_string(),
                dlc_root: dlc_root.to_string_lossy().to_string(),
                vgmstream_resolved: Some(exe.to_string_lossy().to_string()),
                error: Some(msg),
            }
        }
    }
}

pub fn scan_dlc(data_root: &Path) -> Result<Vec<GhwtSongEntry>, String> {
    let dlc_root = ghwt_dlc_root_from_data_root(data_root);
    let entries = fs::read_dir(&dlc_root)
        .map_err(|e| format!("cannot read GHWT DLC folder {}: {e}", dlc_root.display()))?;

    let mut out: Vec<GhwtSongEntry> = vec![];

    for e in entries.flatten() {
        let p = e.path();
        if !p.is_dir() {
            continue;
        }

        let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if !name.starts_with("DLC") {
            continue;
        }

        let song_ini = p.join("song.ini");
        if !song_ini.is_file() {
            continue;
        }
        let raw = fs::read_to_string(&song_ini)
            .map_err(|e| format!("read {}: {e}", song_ini.display()))?;

        let fields = parse_song_ini(&raw);
        let checksum = fields.checksum.unwrap_or_else(|| name.to_string());
        let title = fields.title.unwrap_or_else(|| checksum.clone());
        let artist = fields.artist.unwrap_or_default();

        let content_dir = p.join("Content");
        let music_dir = content_dir.join("MUSIC");
        let preview = music_dir.join(format!("{checksum}_preview.fsb.xen"));

        let stem_candidates = [
            music_dir.join(format!("{checksum}_1.fsb.xen")),
            music_dir.join(format!("{checksum}_2.fsb.xen")),
            music_dir.join(format!("{checksum}_3.fsb.xen")),
        ];
        let stem_fsb_paths: Vec<String> = stem_candidates
            .into_iter()
            .filter(|p| p.is_file())
            .map(|p| p.to_string_lossy().to_string())
            .collect();

        // Only include songs that have at least some audio available.
        if !preview.is_file() && stem_fsb_paths.is_empty() {
            continue;
        }

        let pak = content_dir.join(format!("a{checksum}_song.pak.xen"));
        let pak_path = if pak.is_file() {
            Some(pak.to_string_lossy().to_string())
        } else {
            None
        };

        out.push(GhwtSongEntry {
            checksum,
            title,
            artist,
            year: fields.year,
            dlc_dir: p.to_string_lossy().to_string(),
            preview_fsb_path: preview.to_string_lossy().to_string(),
            stem_fsb_paths,
            pak_path,
        });
    }

    // Stable ordering
    out.sort_by(|a, b| a.checksum.cmp(&b.checksum));
    Ok(out)
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    let digest = h.finalize();
    hex::encode(digest)
}

fn stable_song_id(source_sha256: &str, checksum: &str) -> String {
    // Keep this stable even if we re-import.
    let mut h = Sha256::new();
    h.update(format!("ghwtde|{checksum}|{source_sha256}").as_bytes());
    let digest = h.finalize();
    hex::encode(digest)[0..32].to_string()
}

fn wav_duration_sec(path: &Path) -> Result<f64, String> {
    // Minimal RIFF/WAVE duration parser.
    let bytes = fs::read(path).map_err(|e| format!("read {}: {e}", path.display()))?;
    if bytes.len() < 44 {
        return Err("wav too small".to_string());
    }
    if &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return Err("not a RIFF WAVE".to_string());
    }

    // Parse fmt chunk (assume PCM).
    // This is intentionally minimal and may not handle all WAV variants.
    let mut off = 12;
    let mut byte_rate: Option<u32> = None;
    let mut data_bytes: Option<u32> = None;

    while off + 8 <= bytes.len() {
        let id = &bytes[off..off + 4];
        let size = u32::from_le_bytes(bytes[off + 4..off + 8].try_into().unwrap()) as usize;
        off += 8;
        if off + size > bytes.len() {
            break;
        }

        if id == b"fmt " {
            if size >= 16 {
                byte_rate = Some(u32::from_le_bytes(
                    bytes[off + 8..off + 12].try_into().unwrap(),
                ));
            }
        } else if id == b"data" {
            data_bytes = Some(size as u32);
        }

        // Chunks are padded to even sizes.
        off += size + (size % 2);
        if byte_rate.is_some() && data_bytes.is_some() {
            break;
        }
    }

    let br = byte_rate.ok_or_else(|| "wav missing byte_rate".to_string())?;
    let db = data_bytes.ok_or_else(|| "wav missing data chunk".to_string())?;
    if br == 0 {
        return Err("wav byte_rate=0".to_string());
    }
    Ok(db as f64 / br as f64)
}

fn find_vgmstream_cli(explicit: Option<String>) -> Result<PathBuf, String> {
    if let Some(p) = explicit {
        let pb = PathBuf::from(&p);
        if pb.is_file() {
            return Ok(pb);
        }
        return Err(format!("vgmstream-cli not found at path: {p}"));
    }

    // Fall back to PATH lookup by letting the OS resolve it.
    Ok(PathBuf::from("vgmstream-cli"))
}

// (WAV parsing/mixing moved to crate::wav_mix)

fn decode_to_wav(
    path: &Path,
    wav_out: &Path,
    vgmstream_path: Option<String>,
) -> Result<(), String> {
    if is_riff_wave(path) {
        fs::copy(path, wav_out).map_err(|e| format!("copy wav: {e}"))?;
        return Ok(());
    }

    let vgm = find_vgmstream_cli(vgmstream_path)?;
    let mut cmd = Command::new(vgm);
    cmd.arg("-o").arg(wav_out).arg(path);
    match cmd.output() {
        Ok(cp) => {
            if !cp.status.success() {
                return Err(format!(
                    "vgmstream-cli failed (exit {:?}): {}",
                    cp.status.code(),
                    String::from_utf8_lossy(&cp.stderr)
                ));
            }
            Ok(())
        }
        Err(e) => {
            if e.kind() == std::io::ErrorKind::NotFound {
                Err("vgmstream-cli not found. Install vgmstream and ensure vgmstream-cli is on PATH (or set an explicit path)."
                    .to_string())
            } else {
                Err(format!("vgmstream-cli failed to start: {e}"))
            }
        }
    }
}

fn is_riff_wave(path: &Path) -> bool {
    let Ok(bytes) = fs::read(path) else {
        return false;
    };
    bytes.len() >= 12 && &bytes[0..4] == b"RIFF" && &bytes[8..12] == b"WAVE"
}

pub fn import_preview_to_songpack_to_folder(
    app: Option<&AppHandle>,
    data_root: &Path,
    checksum: &str,
    songs_folder: &Path,
    vgmstream_path: Option<String>,
) -> Result<GhwtImportResult, String> {
    let out_dir = songs_folder.join(format!("ghwt_{checksum}.songpack"));
    if out_dir.exists() {
        return Ok(GhwtImportResult {
            songpack_path: out_dir.to_string_lossy().to_string(),
            used: "existing".to_string(),
        });
    }

    let music_dir = ghwt_dlc_root_from_data_root(data_root)
        .join(checksum)
        .join("Content")
        .join("MUSIC");

    let preview_path = music_dir.join(format!("{checksum}_preview.fsb.xen"));
    let stem_paths = [
        music_dir.join(format!("{checksum}_1.fsb.xen")),
        music_dir.join(format!("{checksum}_2.fsb.xen")),
        music_dir.join(format!("{checksum}_3.fsb.xen")),
    ];
    let stems_present: Vec<PathBuf> = stem_paths.into_iter().filter(|p| p.is_file()).collect();

    if stems_present.is_empty() && !preview_path.is_file() {
        return Err(format!(
            "no audio found (expected preview or stems) under {}",
            music_dir.display()
        ));
    }

    // Build SongPack skeleton.
    emit(
        app,
        ImportProgressEvent {
            song: checksum.to_string(),
            r#type: "stage_start".to_string(),
            id: "init_songpack".to_string(),
            progress: 0.0,
            message: Some("creating songpack folder".to_string()),
            artifact: None,
        },
    );

    fs::create_dir_all(out_dir.join("audio")).map_err(|e| format!("mkdir audio: {e}"))?;
    fs::create_dir_all(out_dir.join("features")).map_err(|e| format!("mkdir features: {e}"))?;
    fs::create_dir_all(out_dir.join("charts")).map_err(|e| format!("mkdir charts: {e}"))?;

    // Read song.ini for metadata.
    let song_ini = ghwt_dlc_root_from_data_root(data_root)
        .join(checksum)
        .join("song.ini");
    let raw_ini =
        fs::read_to_string(&song_ini).map_err(|e| format!("read {}: {e}", song_ini.display()))?;
    let ini = parse_song_ini(&raw_ini);
    let title = ini.title.unwrap_or_else(|| checksum.to_string());
    let artist = ini.artist.unwrap_or_default();

    emit(
        app,
        ImportProgressEvent {
            song: checksum.to_string(),
            r#type: "stage_done".to_string(),
            id: "init_songpack".to_string(),
            progress: 0.1,
            message: None,
            artifact: Some("manifest.json".to_string()),
        },
    );

    // Decode preview audio or stems using vgmstream-cli.
    emit(
        app,
        ImportProgressEvent {
            song: checksum.to_string(),
            r#type: "stage_start".to_string(),
            id: "decode_audio".to_string(),
            progress: 0.1,
            message: Some("decoding preview audio".to_string()),
            artifact: None,
        },
    );

    let wav_out = out_dir.join("audio").join("mix.wav");
    let used: String;
    if stems_present.len() >= 2 {
        // Decode each stem, then mix.
        let mut stem_wavs: Vec<WavPcm16> = vec![];
        for (idx, stem) in stems_present.iter().enumerate() {
            let stem_wav_path = out_dir.join("audio").join(format!("stem_{}.wav", idx + 1));
            decode_to_wav(stem, &stem_wav_path, vgmstream_path.clone())?;
            stem_wavs.push(read_wav_pcm16(&stem_wav_path)?);
        }
        let mixed = mix_wavs(&stem_wavs)?;
        write_wav_pcm16(&wav_out, &mixed)?;
        // Cleanup individual stems (keep only mix for now)
        for i in 0..stem_wavs.len() {
            let _ = fs::remove_file(out_dir.join("audio").join(format!("stem_{}.wav", i + 1)));
        }
        used = "stems".to_string();
    } else {
        decode_to_wav(&preview_path, &wav_out, vgmstream_path.clone())?;
        used = "preview".to_string();
    }

    emit(
        app,
        ImportProgressEvent {
            song: checksum.to_string(),
            r#type: "stage_done".to_string(),
            id: "decode_audio".to_string(),
            progress: 0.6,
            message: None,
            artifact: Some("audio/mix.wav".to_string()),
        },
    );

    // Compute duration + stable ids.
    let wav_bytes = fs::read(&wav_out).map_err(|e| format!("read wav: {e}"))?;
    let source_sha256 = sha256_hex(&wav_bytes);
    let song_id = stable_song_id(&source_sha256, checksum);
    let duration_sec = wav_duration_sec(&wav_out).unwrap_or(0.0);

    let manifest = serde_json::json!({
        "schema_version": "1.0.0",
        "song_id": song_id,
        "title": title,
        "artist": artist,
        "duration_sec": (duration_sec * 1_000_000.0).round() / 1_000_000.0,
        "source": {
            "kind": "ghwt_de",
            "checksum": checksum,
            "data_root": data_root.to_string_lossy(),
            "preview_fsb": preview_path.to_string_lossy(),
            "used": used,
            "audio_sha256": source_sha256,
        },
        "assets": {
            "audio": { "mix_path": "audio/mix.wav" }
        }
    });
    fs::write(
        out_dir.join("manifest.json"),
        serde_json::to_string_pretty(&manifest).map_err(|e| format!("manifest json: {e}"))?,
    )
    .map_err(|e| format!("write manifest: {e}"))?;

    emit(
        app,
        ImportProgressEvent {
            song: checksum.to_string(),
            r#type: "stage_done".to_string(),
            id: "finalize".to_string(),
            progress: 1.0,
            message: Some("import complete".to_string()),
            artifact: Some("manifest.json".to_string()),
        },
    );

    Ok(GhwtImportResult {
        songpack_path: out_dir.to_string_lossy().to_string(),
        used,
    })
}

pub fn import_all_to_folder(
    app: Option<&AppHandle>,
    data_root: &Path,
    songs_folder: &Path,
    vgmstream_path: Option<String>,
) -> Result<Vec<GhwtImportAllResult>, String> {
    let songs = scan_dlc(data_root)?;
    let mut out: Vec<GhwtImportAllResult> = vec![];
    for s in songs {
        emit(
            app,
            ImportProgressEvent {
                song: s.checksum.clone(),
                r#type: "song_start".to_string(),
                id: "bulk_import".to_string(),
                progress: 0.0,
                message: Some("starting".to_string()),
                artifact: None,
            },
        );

        match import_preview_to_songpack_to_folder(
            app,
            data_root,
            &s.checksum,
            songs_folder,
            vgmstream_path.clone(),
        ) {
            Ok(r) => out.push(GhwtImportAllResult {
                ok: true,
                checksum: s.checksum,
                songpack_path: Some(r.songpack_path),
                error: None,
            }),
            Err(e) => out.push(GhwtImportAllResult {
                ok: false,
                checksum: s.checksum,
                songpack_path: None,
                error: Some(e),
            }),
        }
    }
    Ok(out)
}

pub fn import_preview_to_songpack(
    app: &AppHandle,
    data_root: &Path,
    checksum: &str,
    vgmstream_path: Option<String>,
) -> Result<GhwtImportResult, String> {
    // Reuse the existing folder policy (settings override or default per OS).
    let songs_folder = PathBuf::from(crate::get_songs_folder(app.clone())?);
    import_preview_to_songpack_to_folder(
        Some(app),
        data_root,
        checksum,
        &songs_folder,
        vgmstream_path,
    )
}
