//! Sampled piano, mixed into the output alongside the song.
//!
//! Exists so the player can hear the part they are supposed to be playing. The
//! transcription is already note data; this turns it back into sound without
//! needing a rendered stem for it, which matters for the charts that came from
//! MIDI rather than from audio.
//!
//! Notes are scheduled in FRAMES against the transport, not fired from the UI
//! thread. A note triggered when a frame callback happens to notice it lands
//! wherever the buffer boundary fell -- tens of milliseconds of jitter, which on
//! a piano is the difference between a chord and a flam. Starting them inside
//! the render loop puts every onset on the exact frame the chart asked for.

use serde::Deserialize;
use std::path::{Path, PathBuf};

/// The most voices that may sound at once.
///
/// A pedalled piano holds far more strings than a player has fingers, and each
/// one here is an independent resampling read. This is well past what any chart
/// asks for and still cheap; past it, the quietest voice is taken rather than
/// the oldest, because the oldest is often a bass note still carrying the
/// harmony while the quietest is a decayed top note nobody will miss.
const MAX_VOICES: usize = 64;

/// Seconds to fade a voice when its note ends.
///
/// A real damper is not instant, and a hard cut reads as a click rather than a
/// release. Short enough to still feel like the note stopped.
const RELEASE_SEC: f32 = 0.18;

/// Default window in which one pitch may only sound once.
///
/// The chart is meant to cover for the player, not play alongside them. Two
/// strikes of the same note this close together are one note played by two
/// parties, and hearing both is a flam nobody asked for. Wide enough to catch
/// a hand slightly off the beat, short enough that a genuine repeated note --
/// a trill, a repeated quaver -- still speaks twice.
const DEFAULT_GRACE_SEC: f32 = 0.08;

/// One sample from the pack: a recorded note at one pitch and velocity band.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PackSample {
    pub file: String,
    pub root_pitch: u8,
    pub vel_min: u8,
    pub vel_max: u8,
    pub sample_rate_hz: u32,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PackManifest {
    pub name: String,
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub license: String,
    pub samples: Vec<PackSample>,
}

/// Decoded audio for one pack sample.
struct LoadedSample {
    /// Interleaved stereo, normalised.
    data: Vec<f32>,
    frames: usize,
    rate_hz: u32,
    root_pitch: u8,
    vel_min: u8,
    vel_max: u8,
}

pub struct PianoPack {
    pub name: String,
    pub author: String,
    pub license: String,
    samples: Vec<LoadedSample>,
    /// Root pitches present, ascending, for nearest-root lookup.
    roots: Vec<u8>,
}

/// Written by hand, not derived: the samples are tens of megabytes and a
/// derived Debug would try to format all of it the first time anything
/// logged the command carrying this pack.
impl std::fmt::Debug for PianoPack {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PianoPack")
            .field("name", &self.name)
            .field("license", &self.license)
            .field("samples", &self.samples.len())
            .field("roots", &self.roots.len())
            .finish()
    }
}

impl PianoPack {
    /// Read a pack directory: `piano.json` plus the wavs it names.
    pub fn load(dir: &Path) -> Result<Self, String> {
        let manifest_path = dir.join("piano.json");
        let raw = std::fs::read_to_string(&manifest_path)
            .map_err(|e| format!("cannot read {}: {e}", manifest_path.display()))?;
        let manifest: PackManifest =
            serde_json::from_str(&raw).map_err(|e| format!("invalid piano.json: {e}"))?;

        let mut samples = Vec::with_capacity(manifest.samples.len());
        for entry in &manifest.samples {
            let path: PathBuf = dir.join(&entry.file);
            let wav = crate::wav_mix::read_wav_pcm16(&path)
                .map_err(|e| format!("{}: {e}", entry.file))?;
            if wav.channels != 2 {
                return Err(format!("{}: expected stereo, got {}", entry.file, wav.channels));
            }
            let frames = wav.data.len() / 2;
            let mut data = Vec::with_capacity(wav.data.len());
            for v in &wav.data {
                data.push(*v as f32 / 32768.0);
            }
            samples.push(LoadedSample {
                data,
                frames,
                rate_hz: entry.sample_rate_hz.max(1),
                root_pitch: entry.root_pitch,
                vel_min: entry.vel_min,
                vel_max: entry.vel_max,
            });
        }

        if samples.is_empty() {
            return Err("pack contains no samples".to_string());
        }

        let mut roots: Vec<u8> = samples.iter().map(|s| s.root_pitch).collect();
        roots.sort_unstable();
        roots.dedup();

        Ok(Self {
            name: manifest.name,
            author: manifest.author,
            license: manifest.license,
            samples,
            roots,
        })
    }

    pub fn sample_count(&self) -> usize {
        self.samples.len()
    }

    /// The sample to play for a pitch and velocity.
    ///
    /// The pack is sampled in minor thirds, so most notes have no recording of
    /// their own and are played from the nearest root, shifted. Nearest rather
    /// than always-below: a semitone of shift in either direction is far less
    /// audible than two semitones in one.
    fn pick(&self, pitch: u8, velocity: u8) -> Option<(usize, f32)> {
        let root = *self
            .roots
            .iter()
            .min_by_key(|r| (**r as i32 - pitch as i32).abs())?;

        let index = self
            .samples
            .iter()
            .position(|s| s.root_pitch == root && velocity >= s.vel_min && velocity <= s.vel_max)
            .or_else(|| self.samples.iter().position(|s| s.root_pitch == root))?;

        let semitones = pitch as f32 - root as f32;
        Some((index, (semitones / 12.0).exp2()))
    }
}

/// A note the chart asks for, in transport frames.
#[derive(Debug, Clone, Copy)]
pub struct ScheduledNote {
    pub on_frame: u64,
    pub off_frame: u64,
    pub pitch: u8,
    pub velocity: u8,
}

struct Voice {
    sample_index: usize,
    /// Read position in source frames.
    position: f64,
    /// Source frames advanced per output frame: pitch shift and rate conversion
    /// folded into one number, since doing them separately would resample twice.
    step: f64,
    gain: f32,
    pitch: u8,
    /// Transport frame this note ends on; the voice releases itself.
    off_frame: u64,
    /// Frames until this voice is finished releasing, or None while held.
    release_left: Option<u32>,
    release_total: u32,
}

pub struct PianoEngine {
    pack: Option<std::sync::Arc<PianoPack>>,
    voices: Vec<Voice>,
    notes: Vec<ScheduledNote>,
    /// Index of the first note not yet started, for the current position.
    cursor: usize,
    /// Where the cursor was last resolved, so a seek can be detected.
    last_frame: u64,
    gain: f32,
    enabled: bool,
    /// Output rate, remembered so a live note can be started between blocks.
    engine_rate_hz: u32,
    /// Frame each pitch last sounded on, whoever played it. `u64::MAX` means
    /// never, so pitch 0 at frame 0 is not mistaken for a recent note.
    last_sound: [u64; 128],
    grace_sec: f32,
}

impl Default for PianoEngine {
    fn default() -> Self {
        Self {
            pack: None,
            voices: Vec::new(),
            notes: Vec::new(),
            cursor: 0,
            last_frame: 0,
            gain: 0.8,
            enabled: false,
            engine_rate_hz: 48_000,
            last_sound: [u64::MAX; 128],
            grace_sec: DEFAULT_GRACE_SEC,
        }
    }
}

impl PianoEngine {
    pub fn set_pack(&mut self, pack: Option<std::sync::Arc<PianoPack>>) {
        self.pack = pack;
        self.voices.clear();
    }

    pub fn pack_name(&self) -> Option<&str> {
        self.pack.as_ref().map(|p| p.name.as_str())
    }

    pub fn set_enabled(&mut self, on: bool) {
        self.enabled = on;
        if !on {
            self.voices.clear();
        }
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled
    }

    pub fn set_gain(&mut self, gain: f32) {
        self.gain = gain.clamp(0.0, 2.0);
    }

    pub fn set_grace_sec(&mut self, sec: f32) {
        self.grace_sec = sec.clamp(0.0, 1.0);
    }

    /// Whether this pitch has already sounded too recently to sound again.
    ///
    /// Asked of BOTH sources, which is what makes it symmetric: the player
    /// ahead of the beat suppresses the chart's note, and the player behind it
    /// is suppressed by the chart's. Either way one note is heard, and it is
    /// whichever arrived first.
    fn already_sounding(&self, pitch: u8, now: u64) -> bool {
        let last = self.last_sound[pitch as usize % 128];
        if last == u64::MAX || now < last {
            return false;
        }
        (now - last) < (self.grace_sec * self.engine_rate_hz.max(1) as f32) as u64
    }

    fn mark_sounded(&mut self, pitch: u8, frame: u64) {
        self.last_sound[pitch as usize % 128] = frame;
    }

    /// Sound a note immediately, for a key the player just pressed.
    ///
    /// Separate from the schedule because it has no end time: a live note lasts
    /// until the finger leaves, which nobody can know in advance. Scheduling it
    /// with a guessed length would either cut the note off under the player or
    /// leave it ringing after they lifted.
    pub fn note_on(&mut self, pitch: u8, velocity: u8) {
        let now = self.last_frame;
        if self.already_sounding(pitch, now) {
            // The chart has just played this note. Sounding it again is the
            // doubling; the player still SEES their key light up, so nothing
            // about their own playing is hidden.
            return;
        }
        let note = ScheduledNote {
            on_frame: 0,
            off_frame: u64::MAX,
            pitch,
            velocity: velocity.max(1),
        };
        self.start_voice(note, 0, self.engine_rate_hz.max(1));
        self.mark_sounded(pitch, now);
    }

    /// Let a live note go.
    ///
    /// Only voices with no end frame: a scheduled note that happens to share
    /// this pitch is the chart's, and the player releasing their own key should
    /// not silence it.
    pub fn note_off(&mut self, pitch: u8) {
        let release = ((RELEASE_SEC * self.engine_rate_hz.max(1) as f32) as u32).max(1);
        for voice in &mut self.voices {
            if voice.pitch == pitch && voice.off_frame == u64::MAX && voice.release_left.is_none() {
                voice.release_left = Some(release);
                voice.release_total = release;
            }
        }
    }

    /// Replace the schedule. Notes must be sorted by `on_frame`.
    pub fn set_notes(&mut self, mut notes: Vec<ScheduledNote>) {
        notes.sort_by_key(|n| n.on_frame);
        self.notes = notes;
        self.cursor = 0;
        self.last_frame = 0;
        self.voices.clear();
    }

    pub fn note_count(&self) -> usize {
        self.notes.len()
    }

    /// Re-place the cursor after a seek.
    fn resync(&mut self, frame: u64) {
        self.last_sound = [u64::MAX; 128];
        // Binary search rather than a walk: a seek to the end of a long chart
        // would otherwise step through every note to get there.
        self.cursor = self.notes.partition_point(|n| n.on_frame < frame);
        self.voices.clear();
    }

    /// Mix scheduled notes for `frames` of output starting at `start_frame`.
    ///
    /// `out` already holds the song; this adds to it rather than replacing, so
    /// the piano sits over the mix at whatever gain is set.
    pub fn mix(
        &mut self,
        out: &mut [f32],
        channels: usize,
        start_frame: u64,
        engine_rate_hz: u32,
    ) {
        if channels == 0 || self.pack.is_none() {
            return;
        }
        self.engine_rate_hz = engine_rate_hz;

        let frames = out.len() / channels;
        if frames == 0 {
            return;
        }

        // A jump either way means the playhead moved under us -- a seek, a loop
        // wrap, or the first block after a load. Walking forward from a stale
        // cursor would fire every note in between at once.
        let expected = self.last_frame;
        if start_frame < expected || start_frame.saturating_sub(expected) > frames as u64 * 4 {
            self.resync(start_frame);
        }
        self.last_frame = start_frame + frames as u64;

        let end_frame = start_frame + frames as u64;

        // Start everything whose onset lands inside this block, at the exact
        // frame it asked for. Only the SCHEDULE is gated on `enabled` -- a live
        // note is the player pressing a key and always sounds.
        while self.enabled && self.cursor < self.notes.len() {
            let note = self.notes[self.cursor];
            if note.on_frame >= end_frame {
                break;
            }
            let offset = note.on_frame.saturating_sub(start_frame) as usize;
            // The player got here first: this is their note, not ours.
            if !self.already_sounding(note.pitch, note.on_frame) {
                self.start_voice(note, offset.min(frames), engine_rate_hz);
                self.mark_sounded(note.pitch, note.on_frame);
            }
            self.cursor += 1;
        }

        // Release voices whose notes have ended. Each voice carries its own end
        // frame, so this costs nothing per note in the chart -- searching the
        // note list here would make a long piece quadratic.
        let release_frames = ((RELEASE_SEC * engine_rate_hz as f32) as u32).max(1);
        for voice in &mut self.voices {
            if voice.release_left.is_none()
                && voice.off_frame != u64::MAX
                && voice.off_frame <= end_frame
            {
                voice.release_left = Some(release_frames);
                voice.release_total = release_frames;
            }
        }

        let pack = self.pack.as_ref().expect("checked above");
        let master = self.gain;

        self.voices.retain_mut(|voice| {
            let sample = &pack.samples[voice.sample_index];
            let mut alive = true;

            for frame_idx in 0..frames {
                let src = voice.position;

                // Not started yet: this voice's onset falls later in the block.
                // Reading here would index the sample from a negative position.
                if src < 0.0 {
                    voice.position += voice.step;
                    continue;
                }

                if src >= sample.frames as f64 - 1.0 {
                    alive = false;
                    break;
                }

                // Linear interpolation. Higher-order would be better on paper,
                // but the shift is at most a semitone and a half either way --
                // the artefacts sit far below the noise floor of the recording.
                let i0 = src.floor() as usize;
                let frac = (src - i0 as f64) as f32;
                let i1 = i0 + 1;

                let mut env = voice.gain * master;
                if let Some(left) = voice.release_left {
                    if left == 0 {
                        alive = false;
                        break;
                    }
                    env *= left as f32 / voice.release_total as f32;
                    voice.release_left = Some(left - 1);
                }

                let base = frame_idx * channels;
                for ch in 0..channels.min(2) {
                    let a = sample.data[i0 * 2 + ch];
                    let b = sample.data[i1 * 2 + ch];
                    out[base + ch] += (a + (b - a) * frac) * env;
                }

                voice.position += voice.step;
            }

            alive
        });
    }

    fn start_voice(&mut self, note: ScheduledNote, offset_frames: usize, engine_rate_hz: u32) {
        let Some(pack) = self.pack.as_ref() else {
            return;
        };
        let Some((index, ratio)) = pack.pick(note.pitch, note.velocity) else {
            return;
        };
        let sample = &pack.samples[index];

        if self.voices.len() >= MAX_VOICES {
            // Quietest, not oldest: the oldest is often a bass note still
            // holding the harmony, while the quietest has already decayed to
            // something nobody is listening to.
            if let Some(idx) = self
                .voices
                .iter()
                .enumerate()
                .min_by(|a, b| a.1.gain.total_cmp(&b.1.gain))
                .map(|(i, _)| i)
            {
                self.voices.remove(idx);
            }
        }

        // Pitch shift and rate conversion in one step, so the source is read
        // once rather than resampled twice.
        let step = ratio as f64 * (sample.rate_hz as f64 / engine_rate_hz.max(1) as f64);

        self.voices.push(Voice {
            sample_index: index,
            // Negative start means "begins partway into this block": the voice
            // reads silence until the frame the chart actually asked for.
            position: -(offset_frames as f64) * step,
            step,
            gain: (note.velocity as f32 / 127.0).powf(0.6),
            pitch: note.pitch,
            off_frame: note.off_frame,
            release_left: None,
            release_total: 1,
        });
    }
}
