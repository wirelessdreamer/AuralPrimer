use crate::wav_mix::{mix_wavs, read_wav_pcm16, write_wav_pcm16, WavPcm16};
use midly::{Smf, Timing, TrackEventKind};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct StemMidiCreateRequest {
    pub title: String,
    pub artist: String,

    /// One or more absolute paths to WAV stems.
    pub stem_wav_paths: Vec<String>,

    /// Absolute path to a MIDI file.
    pub midi_path: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct StemMidiCreateResult {
    pub songpack_path: String,
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    let digest = h.finalize();
    hex::encode(digest)
}

fn sanitize_for_folder(s: &str) -> String {
    // keep it deterministic, cross-platform, and readable
    let mut out = String::new();
    for ch in s.chars() {
        let ok = ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' || ch == ' ';
        if ok {
            out.push(ch);
        } else {
            out.push('_');
        }
    }
    out.trim().replace(' ', "_")
}

fn stable_song_id(audio_sha256: &str, midi_sha256: &str) -> String {
    let mut h = Sha256::new();
    h.update(format!("stem_midi|{audio_sha256}|{midi_sha256}").as_bytes());
    let digest = h.finalize();
    hex::encode(digest)[0..32].to_string()
}

fn wav_duration_sec_from_pcm(w: &WavPcm16) -> f64 {
    let frames = (w.data.len() as f64) / (w.channels as f64);
    frames / (w.sample_rate as f64)
}

/// Parse a MIDI file and return note events as JSON-ready objects.
///
/// Implementation notes:
/// - We treat tempo as constant 120bpm unless a SetTempo is present.
/// - We only emit a single track ("midi").
/// - Timestamps are in seconds.
fn midi_to_events_json(midi_bytes: &[u8]) -> Result<serde_json::Value, String> {
    let smf = Smf::parse(midi_bytes).map_err(|e| format!("invalid midi: {e:?}"))?;

    let tpq = match smf.header.timing {
        Timing::Metrical(t) => t.as_int() as u32,
        Timing::Timecode(_, _) => return Err("unsupported MIDI timing (SMPTE timecode)".to_string()),
    };

    // Default: 120 bpm => 500_000 us per beat.
    let mut tempo_us_per_beat: u32 = 500_000;

    // Gather events across tracks in a naive way: keep per-track time.
    // We'll only emit note events; the schema is flexible (notes array is untyped).
    #[derive(Clone, Copy, Debug)]
    struct NoteOn {
        t_ticks: u32,
        vel: u8,
    }

    let mut notes_out: Vec<serde_json::Value> = vec![];
    let mut open_notes: std::collections::BTreeMap<(u8, u8), NoteOn> = std::collections::BTreeMap::new();

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
                                        "track_id": "midi",
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
                                    "track_id": "midi",
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

    Ok(serde_json::json!({
        "events_version": "1.0.0",
        "tracks": [{"track_id": "midi", "role": "other", "name": "MIDI"}],
        "notes": notes_out
    }))
}

fn ticks_to_sec(t_ticks: u32, tpq: u32, tempo_us_per_beat: u32) -> f64 {
    if tpq == 0 {
        return 0.0;
    }
    let beats = (t_ticks as f64) / (tpq as f64);
    let sec_per_beat = (tempo_us_per_beat as f64) / 1_000_000.0;
    beats * sec_per_beat
}

pub fn create_songpack(req: StemMidiCreateRequest, songs_folder: &Path) -> Result<StemMidiCreateResult, String> {
    if req.stem_wav_paths.is_empty() {
        return Err("at least one stem WAV is required".to_string());
    }

    let stems: Vec<PathBuf> = req.stem_wav_paths.iter().map(PathBuf::from).collect();
    for s in &stems {
        if !s.is_file() {
            return Err(format!("stem not found: {}", s.display()));
        }
    }
    let midi_path = PathBuf::from(&req.midi_path);
    if !midi_path.is_file() {
        return Err(format!("midi not found: {}", midi_path.display()));
    }

    // Read + validate stems
    let mut wavs: Vec<WavPcm16> = vec![];
    for s in &stems {
        wavs.push(read_wav_pcm16(s)?);
    }
    let mixed = if wavs.len() == 1 { wavs.remove(0) } else { mix_wavs(&wavs)? };

    // Build output folder name.
    let base = format!("{}_{}", sanitize_for_folder(&req.artist), sanitize_for_folder(&req.title));
    let mut out_dir = songs_folder.join(format!("stem_midi_{base}.songpack"));

    // Avoid overwriting: append a numeric suffix.
    if out_dir.exists() {
        for i in 2..=9999 {
            let candidate = songs_folder.join(format!("stem_midi_{base}_{i}.songpack"));
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
    fs::create_dir_all(out_dir.join("charts")).map_err(|e| format!("mkdir charts: {e}"))?;

    let mix_path = out_dir.join("audio").join("mix.wav");
    write_wav_pcm16(&mix_path, &mixed)?;

    // Copy midi verbatim
    let midi_out_path = out_dir.join("features").join("notes.mid");
    fs::copy(&midi_path, &midi_out_path).map_err(|e| format!("copy midi: {e}"))?;

    // Compute IDs + duration
    let wav_bytes = fs::read(&mix_path).map_err(|e| format!("read mix.wav: {e}"))?;
    let midi_bytes = fs::read(&midi_path).map_err(|e| format!("read midi: {e}"))?;
    let audio_sha256 = sha256_hex(&wav_bytes);
    let midi_sha256 = sha256_hex(&midi_bytes);
    let song_id = stable_song_id(&audio_sha256, &midi_sha256);

    let duration_sec = wav_duration_sec_from_pcm(&mixed);

    // Minimal events.json from MIDI.
    let events_json = midi_to_events_json(&midi_bytes)?;
    fs::write(
        out_dir.join("features").join("events.json"),
        serde_json::to_string_pretty(&events_json).map_err(|e| format!("events json: {e}"))?,
    )
    .map_err(|e| format!("write events.json: {e}"))?;

    let manifest = serde_json::json!({
        "schema_version": "1.0.0",
        "song_id": song_id,
        "title": req.title,
        "artist": req.artist,
        "duration_sec": (duration_sec * 1_000_000.0).round() / 1_000_000.0,
        "source": {
            "kind": "stem_midi",
            "audio_sha256": audio_sha256,
            "midi_sha256": midi_sha256,
            "stems": stems.iter().map(|p| p.to_string_lossy().to_string()).collect::<Vec<_>>(),
            "midi": midi_path.to_string_lossy(),
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

    Ok(StemMidiCreateResult {
        songpack_path: out_dir.to_string_lossy().to_string(),
    })
}
