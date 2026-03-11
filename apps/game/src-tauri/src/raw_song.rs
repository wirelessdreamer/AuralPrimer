use crate::wav_mix::{mix_wavs, read_wav_pcm16, write_wav_pcm16, WavPcm16};
use midly::{Smf, Timing, TrackEventKind};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ImportRawSongFolderRequest {
    /// Absolute path to a folder containing stem WAVs and one-or-more MIDI files.
    pub folder_path: String,

    /// Optional overrides. If omitted, we derive a title from the folder name.
    pub title: Option<String>,
    pub artist: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ImportRawSongFolderResult {
    pub songpack_path: String,
    pub stems_count: usize,
    pub midi_files_count: usize,
    pub lyrics_included: bool,
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    let digest = h.finalize();
    hex::encode(digest)
}

fn sanitize_id(s: &str) -> String {
    let mut out = String::new();
    for ch in s.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
        } else if ch == ' ' || ch == '-' || ch == '_' {
            out.push('_');
        }
    }
    while out.contains("__") {
        out = out.replace("__", "_");
    }
    out.trim_matches('_').to_string()
}

fn wav_duration_sec_from_pcm(w: &WavPcm16) -> f64 {
    let frames = (w.data.len() as f64) / (w.channels as f64);
    frames / (w.sample_rate as f64)
}

fn quantize(t: f64, q: f64) -> f64 {
    (t / q).round() * q
}

fn generate_beats(duration_sec: f64, bpm: f64, beats_per_bar: i32) -> serde_json::Value {
    let bpm = if bpm > 0.0 { bpm } else { 120.0 };
    let period = 60.0 / bpm;
    let mut beats: Vec<serde_json::Value> = vec![];
    let mut bar = 0;
    let mut beat_in_bar = 0;
    let mut t = 0.0;
    while t <= duration_sec + 1e-9 {
        let strength = if beat_in_bar == 0 { 1.0 } else { 0.5 };
        beats.push(serde_json::json!({
            "t": quantize(t, 1e-6),
            "bar": bar,
            "beat": beat_in_bar,
            "strength": strength,
        }));
        beat_in_bar += 1;
        if beat_in_bar >= beats_per_bar {
            beat_in_bar = 0;
            bar += 1;
        }
        t += period;
    }

    serde_json::json!({"beats_version": "1.0.0", "beats": beats})
}

fn generate_tempo_map(bpm: f64) -> serde_json::Value {
    let bpm = if bpm > 0.0 { bpm } else { 120.0 };
    serde_json::json!({
        "tempo_version": "1.0.0",
        "segments": [{"t0": 0.0, "bpm": (bpm * 1000.0).round()/1000.0, "time_signature": "4/4"}]
    })
}

fn generate_sections(duration_sec: f64, bpm: f64, bars_per_section: i32) -> serde_json::Value {
    let bpm = if bpm > 0.0 { bpm } else { 120.0 };
    let sec_per_bar = (60.0 / bpm) * 4.0;
    let sec_per_section = (sec_per_bar * (bars_per_section as f64)).max(1.0);
    let mut sections: Vec<serde_json::Value> = vec![];
    let mut t0 = 0.0;
    let mut idx = 0;
    while t0 < duration_sec - 1e-9 {
        let t1 = (t0 + sec_per_section).min(duration_sec);
        sections.push(serde_json::json!({
            "t0": quantize(t0, 1e-6),
            "t1": quantize(t1, 1e-6),
            "label": format!("section_{idx}"),
        }));
        t0 = t1;
        idx += 1;
    }

    if sections.is_empty() {
        sections.push(serde_json::json!({"t0": 0.0, "t1": quantize(duration_sec, 1e-6), "label": "section_0"}));
    }

    serde_json::json!({"sections_version": "1.0.0", "sections": sections})
}

fn ticks_to_sec(t_ticks: u32, tpq: u32, tempo_us_per_beat: u32) -> f64 {
    if tpq == 0 {
        return 0.0;
    }
    let beats = (t_ticks as f64) / (tpq as f64);
    let sec_per_beat = (tempo_us_per_beat as f64) / 1_000_000.0;
    beats * sec_per_beat
}

fn role_from_track_id(id: &str) -> &'static str {
    let s = id.to_ascii_lowercase();
    if s.contains("drum") {
        "drums"
    } else if s.contains("bass") {
        "bass"
    } else if s.contains("guitar") {
        "guitar"
    } else if s.contains("vocal") {
        "vocals"
    } else if s.contains("synth") || s.contains("keys") || s.contains("keyboard") {
        "keys"
    } else {
        "other"
    }
}

fn midi_bytes_to_notes(
    midi_bytes: &[u8],
    track_id: &str,
) -> Result<Vec<serde_json::Value>, String> {
    let smf = Smf::parse(midi_bytes).map_err(|e| format!("invalid midi: {e:?}"))?;
    let tpq = match smf.header.timing {
        Timing::Metrical(t) => t.as_int() as u32,
        Timing::Timecode(_, _) => {
            return Err("unsupported MIDI timing (SMPTE timecode)".to_string())
        }
    };

    // Default: 120 bpm => 500_000 us per beat.
    let mut tempo_us_per_beat: u32 = 500_000;

    #[derive(Clone, Copy, Debug)]
    struct NoteOn {
        t_ticks: u32,
        vel: u8,
    }

    let mut notes_out: Vec<serde_json::Value> = vec![];
    let mut open_notes: std::collections::BTreeMap<(u8, u8), NoteOn> =
        std::collections::BTreeMap::new();

    for track in &smf.tracks {
        let mut t_ticks: u32 = 0;
        for ev in track {
            t_ticks = t_ticks.saturating_add(ev.delta.as_int() as u32);
            match &ev.kind {
                TrackEventKind::Meta(m) => {
                    if let midly::MetaMessage::Tempo(us) = m {
                        tempo_us_per_beat = (*us).as_int();
                    }
                }
                TrackEventKind::Midi { channel, message } => {
                    let ch = channel.as_int() as u8;
                    match message {
                        midly::MidiMessage::NoteOn { key, vel } => {
                            let pitch = key.as_int() as u8;
                            let v = vel.as_int() as u8;
                            if v == 0 {
                                // treat NoteOn vel=0 as NoteOff
                                if let Some(on) = open_notes.remove(&(ch, pitch)) {
                                    notes_out.push(serde_json::json!({
                                        "track_id": track_id,
                                        "t_on": ticks_to_sec(on.t_ticks, tpq, tempo_us_per_beat),
                                        "t_off": ticks_to_sec(t_ticks, tpq, tempo_us_per_beat),
                                        "pitch": {"type": "midi", "value": pitch},
                                        "velocity": (on.vel as f64) / 127.0,
                                        "confidence": 1.0,
                                        "source": "midi"
                                    }));
                                }
                            } else {
                                open_notes.insert((ch, pitch), NoteOn { t_ticks, vel: v });
                            }
                        }
                        midly::MidiMessage::NoteOff { key, .. } => {
                            let pitch = key.as_int() as u8;
                            if let Some(on) = open_notes.remove(&(ch, pitch)) {
                                notes_out.push(serde_json::json!({
                                    "track_id": track_id,
                                    "t_on": ticks_to_sec(on.t_ticks, tpq, tempo_us_per_beat),
                                    "t_off": ticks_to_sec(t_ticks, tpq, tempo_us_per_beat),
                                    "pitch": {"type": "midi", "value": pitch},
                                    "velocity": (on.vel as f64) / 127.0,
                                    "confidence": 1.0,
                                    "source": "midi"
                                }));
                            }
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        }
    }

    // Sort by t_on for stable output.
    notes_out.sort_by(|a, b| {
        let ta = a.get("t_on").and_then(|x| x.as_f64()).unwrap_or(0.0);
        let tb = b.get("t_on").and_then(|x| x.as_f64()).unwrap_or(0.0);
        ta.partial_cmp(&tb).unwrap_or(std::cmp::Ordering::Equal)
    });

    Ok(notes_out)
}

fn find_karaoke_json(root: &Path) -> Option<PathBuf> {
    // Look for lyrics_karaoke.karaoke.json (produced by PsalmsKaraoke) anywhere under root.
    // Prefer a path containing "newoutput" if multiple are present.
    fn rec(dir: &Path, depth: usize, out: &mut Vec<PathBuf>) {
        if depth > 6 {
            return;
        }
        let Ok(rd) = fs::read_dir(dir) else {
            return;
        };
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                rec(&p, depth + 1, out);
            } else if p.is_file() {
                if let Some(name) = p.file_name().and_then(|s| s.to_str()) {
                    if name == "lyrics_karaoke.karaoke.json" {
                        out.push(p);
                    }
                }
            }
        }
    }

    let mut matches: Vec<PathBuf> = vec![];
    rec(root, 0, &mut matches);
    if matches.is_empty() {
        return None;
    }

    matches.sort();
    for p in &matches {
        if p.to_string_lossy()
            .to_ascii_lowercase()
            .contains("newoutput")
        {
            return Some(p.clone());
        }
    }
    Some(matches[0].clone())
}

fn pad_wavs_to_max_len(wavs: &mut [WavPcm16]) {
    if wavs.is_empty() {
        return;
    }
    let max_len = wavs.iter().map(|w| w.data.len()).max().unwrap_or(0);
    for w in wavs {
        if w.data.len() < max_len {
            w.data.resize(max_len, 0);
        }
    }
}

pub fn import_raw_song_folder(
    req: ImportRawSongFolderRequest,
    songs_folder: &Path,
) -> Result<ImportRawSongFolderResult, String> {
    let root = PathBuf::from(&req.folder_path);
    if !root.is_dir() {
        return Err(format!("folder not found: {}", root.display()));
    }

    // Scan only the root level for stems/midi to avoid picking up intermediate outputs.
    let mut stems: Vec<PathBuf> = vec![];
    let mut midis: Vec<PathBuf> = vec![];
    let Ok(rd) = fs::read_dir(&root) else {
        return Err(format!("cannot read_dir: {}", root.display()));
    };
    for e in rd.flatten() {
        let p = e.path();
        if !p.is_file() {
            continue;
        }
        let ext = p
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if ext == "wav" {
            stems.push(p);
        } else if ext == "mid" || ext == "midi" {
            midis.push(p);
        }
    }
    stems.sort();
    midis.sort();

    if stems.is_empty() {
        return Err("no stem wav files found in folder".to_string());
    }
    if midis.is_empty() {
        return Err("no midi files found in folder".to_string());
    }

    // Read + validate stems.
    let mut wavs: Vec<WavPcm16> = vec![];
    for s in &stems {
        wavs.push(read_wav_pcm16(s)?);
    }
    // Be tolerant of tiny duration mismatches.
    pad_wavs_to_max_len(&mut wavs);
    let mixed = if wavs.len() == 1 {
        wavs.remove(0)
    } else {
        mix_wavs(&wavs)?
    };

    // Derive defaults.
    let folder_name = root
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("Raw Song");
    let title = req.title.unwrap_or_else(|| folder_name.to_string());
    let artist = req.artist.unwrap_or_else(|| "".to_string());

    // Build output folder name.
    let base = format!("{}_{}", sanitize_id(&artist), sanitize_id(&title));
    let base = base.trim_matches('_');
    let mut out_dir = songs_folder.join(format!("raw_{base}.songpack"));
    if out_dir.exists() {
        for i in 2..=9999 {
            let candidate = songs_folder.join(format!("raw_{base}_{i}.songpack"));
            if !candidate.exists() {
                out_dir = candidate;
                break;
            }
        }
        if out_dir.exists() {
            return Err("unable to choose a unique output songpack path".to_string());
        }
    }

    fs::create_dir_all(out_dir.join("audio")).map_err(|e| format!("mkdir audio: {e}"))?;
    fs::create_dir_all(out_dir.join("features")).map_err(|e| format!("mkdir features: {e}"))?;
    fs::create_dir_all(out_dir.join("features").join("midi"))
        .map_err(|e| format!("mkdir features/midi: {e}"))?;
    fs::create_dir_all(out_dir.join("charts")).map_err(|e| format!("mkdir charts: {e}"))?;

    let mix_path = out_dir.join("audio").join("mix.wav");
    write_wav_pcm16(&mix_path, &mixed)?;

    // MIDI: pick a default notes.mid (prefer Guitar).
    let preferred = midis
        .iter()
        .find(|p| {
            p.file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .contains("Guitar")
        })
        .cloned()
        .unwrap_or_else(|| midis[0].clone());
    fs::copy(&preferred, out_dir.join("features").join("notes.mid"))
        .map_err(|e| format!("copy notes.mid: {e}"))?;

    // Copy all midis into features/midi and build events.json from all.
    let mut tracks_out: Vec<serde_json::Value> = vec![];
    let mut notes_out: Vec<serde_json::Value> = vec![];
    let mut midi_shas: Vec<String> = vec![];

    for m in &midis {
        let bytes = fs::read(m).map_err(|e| format!("read midi {}: {e}", m.display()))?;
        midi_shas.push(sha256_hex(&bytes));

        let track_name = m.file_stem().and_then(|s| s.to_str()).unwrap_or("midi");
        let track_id = sanitize_id(track_name);
        let role = role_from_track_id(&track_id);
        tracks_out
            .push(serde_json::json!({"track_id": track_id, "role": role, "name": track_name}));

        let dst = out_dir
            .join("features")
            .join("midi")
            .join(format!("{track_id}.mid"));
        fs::write(&dst, &bytes).map_err(|e| format!("write midi copy {}: {e}", dst.display()))?;

        let mut notes = midi_bytes_to_notes(&bytes, &track_id)?;
        notes_out.append(&mut notes);
    }

    // Sort notes across all tracks.
    notes_out.sort_by(|a, b| {
        let ta = a.get("t_on").and_then(|x| x.as_f64()).unwrap_or(0.0);
        let tb = b.get("t_on").and_then(|x| x.as_f64()).unwrap_or(0.0);
        ta.partial_cmp(&tb).unwrap_or(std::cmp::Ordering::Equal)
    });

    let events_json = serde_json::json!({
        "events_version": "1.0.0",
        "tracks": tracks_out,
        "notes": notes_out,
    });
    fs::write(
        out_dir.join("features").join("events.json"),
        serde_json::to_string_pretty(&events_json).map_err(|e| format!("events json: {e}"))?,
    )
    .map_err(|e| format!("write events.json: {e}"))?;

    // Optional lyrics: if PsalmsKaraoke output exists, write features/lyrics.json.
    let mut lyrics_included = false;
    if let Some(karaoke_json_path) = find_karaoke_json(&root) {
        if let Ok(raw) = fs::read_to_string(&karaoke_json_path) {
            if serde_json::from_str::<serde_json::Value>(&raw).is_ok() {
                fs::write(out_dir.join("features").join("lyrics.json"), raw)
                    .map_err(|e| format!("write lyrics.json: {e}"))?;
                lyrics_included = true;
            }
        }
    }

    // Provide minimal required rhythm scaffolding for gameplay.
    let duration_sec = wav_duration_sec_from_pcm(&mixed);
    let bpm = 120.0;
    let beats = generate_beats(duration_sec, bpm, 4);
    let tempo = generate_tempo_map(bpm);
    let sections = generate_sections(duration_sec, bpm, 8);

    fs::write(
        out_dir.join("features").join("beats.json"),
        serde_json::to_string_pretty(&beats).map_err(|e| format!("beats json: {e}"))?,
    )
    .map_err(|e| format!("write beats.json: {e}"))?;

    fs::write(
        out_dir.join("features").join("tempo_map.json"),
        serde_json::to_string_pretty(&tempo).map_err(|e| format!("tempo json: {e}"))?,
    )
    .map_err(|e| format!("write tempo_map.json: {e}"))?;

    fs::write(
        out_dir.join("features").join("sections.json"),
        serde_json::to_string_pretty(&sections).map_err(|e| format!("sections json: {e}"))?,
    )
    .map_err(|e| format!("write sections.json: {e}"))?;

    // MVP chart: one target per beat.
    let beat_items = beats
        .get("beats")
        .and_then(|x| x.as_array())
        .cloned()
        .unwrap_or_default();
    let targets: Vec<serde_json::Value> = beat_items
        .iter()
        .map(|b| serde_json::json!({"t": b.get("t").and_then(|x| x.as_f64()).unwrap_or(0.0), "lane": "beat"}))
        .collect();
    let chart = serde_json::json!({
        "chart_version": "1.0.0",
        "mode": "beats_only",
        "difficulty": "easy",
        "targets": targets,
    });
    fs::write(
        out_dir.join("charts").join("easy.json"),
        serde_json::to_string_pretty(&chart).map_err(|e| format!("chart json: {e}"))?,
    )
    .map_err(|e| format!("write chart easy.json: {e}"))?;

    // IDs
    let wav_bytes = fs::read(&mix_path).map_err(|e| format!("read mix.wav: {e}"))?;
    let audio_sha256 = sha256_hex(&wav_bytes);
    midi_shas.sort();
    let mut h = Sha256::new();
    h.update(format!("raw|{audio_sha256}|{}", midi_shas.join("|")).as_bytes());
    let song_id = hex::encode(h.finalize())[0..32].to_string();

    let manifest = serde_json::json!({
        "schema_version": "1.0.0",
        "song_id": song_id,
        "title": title,
        "artist": artist,
        "duration_sec": (duration_sec * 1_000_000.0).round() / 1_000_000.0,
        "source": {
            "kind": "raw_song_data",
            "folder": root.to_string_lossy(),
            "audio_sha256": audio_sha256,
            "midi_sha256": midi_shas,
            "stems": stems.iter().map(|p| p.to_string_lossy().to_string()).collect::<Vec<_>>(),
            "midis": midis.iter().map(|p| p.to_string_lossy().to_string()).collect::<Vec<_>>(),
        },
        "assets": {
            "audio": {"mix_path": "audio/mix.wav"},
            "midi": {"notes_path": "features/notes.mid"}
        }
    });
    fs::write(
        out_dir.join("manifest.json"),
        serde_json::to_string_pretty(&manifest).map_err(|e| format!("manifest json: {e}"))?,
    )
    .map_err(|e| format!("write manifest: {e}"))?;

    Ok(ImportRawSongFolderResult {
        songpack_path: out_dir.to_string_lossy().to_string(),
        stems_count: stems.len(),
        midi_files_count: midis.len(),
        lyrics_included,
    })
}
