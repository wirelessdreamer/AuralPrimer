//! Metronome clicks, mixed into the output alongside the song.
//!
//! In the engine rather than in the webview for the same reason the piano is:
//! the clicks are placed on the exact song frame of each beat, on the device
//! the song plays on, and they follow the playback rate and every seek without
//! the UI having to notice. The webview's WebAudio clicks went to whatever
//! device the webview chose, if any, and drifted against the song.
//!
//! The grid is the one the UI shows: quarter notes from the start of the song
//! at a constant tempo, the first beat of each bar accented.

/// Length of one click.
const CLICK_SEC: f32 = 0.05;
const DOWNBEAT_HZ: f32 = 1760.0;
const BEAT_HZ: f32 = 880.0;

pub struct Metronome {
    enabled: bool,
    volume: f32,
    bpm: f64,
    beats_per_bar: u32,
    /// The click sounding now: output frames into it, its pitch.
    click: Option<(u32, f32)>,
}

impl Default for Metronome {
    fn default() -> Self {
        Self {
            enabled: false,
            volume: 0.25,
            bpm: 120.0,
            beats_per_bar: 4,
            click: None,
        }
    }
}

impl Metronome {
    pub fn set(&mut self, enabled: bool, volume: f32, bpm: f64, beats_per_bar: u32) {
        self.enabled = enabled;
        self.volume = if volume.is_finite() {
            volume.clamp(0.0, 1.0)
        } else {
            0.25
        };
        self.bpm = if bpm.is_finite() && bpm > 0.0 {
            bpm.clamp(20.0, 400.0)
        } else {
            120.0
        };
        self.beats_per_bar = beats_per_bar.max(1);
        if !enabled {
            self.click = None;
        }
    }

    /// Add this block's clicks to `out`. `start_frame` is the song frame at the
    /// block's start and `rate` the song frames per output frame, as the piano
    /// takes them; nothing new starts unless the transport is playing.
    pub fn mix(
        &mut self,
        out: &mut [f32],
        channels: usize,
        start_frame: u64,
        engine_rate_hz: u32,
        playing: bool,
        rate: f64,
    ) {
        if !self.enabled || channels == 0 {
            return;
        }
        let frames = out.len() / channels;
        let rate_hz = engine_rate_hz.max(1) as f64;
        let rate = if rate.is_finite() && rate > 0.0 {
            rate
        } else {
            1.0
        };
        let beat_frames = 60.0 / self.bpm * rate_hz;
        let click_frames = (CLICK_SEC * engine_rate_hz.max(1) as f32) as u32;
        let beats_per_bar = self.beats_per_bar as u64;

        // Beats whose song frame falls in [start, start + frames * rate), as
        // output-frame offsets: half-open, so a beat on a block boundary is
        // sounded by exactly one block. Worked out one at a time, not
        // collected: this runs on the audio thread, which must not allocate.
        let start = start_frame as f64;
        let end = start + frames as f64 * rate;
        let mut k = (start / beat_frames).ceil() as u64;
        let next_beat = |k: u64| -> Option<(usize, f32)> {
            let song = k as f64 * beat_frames;
            if !playing || song >= end {
                return None;
            }
            let at = (((song - start) / rate) as usize).min(frames.saturating_sub(1));
            let hz = if k % beats_per_bar == 0 {
                DOWNBEAT_HZ
            } else {
                BEAT_HZ
            };
            Some((at, hz))
        };
        let mut next = next_beat(k);

        for frame in 0..frames {
            if let Some((at, hz)) = next {
                if at == frame {
                    self.click = Some((0, hz));
                    k += 1;
                    next = next_beat(k);
                }
            }
            let Some((n, hz)) = self.click else {
                continue;
            };
            if n >= click_frames {
                self.click = None;
                continue;
            }
            // A sine from phase zero, decaying fast: starts without a pop and
            // reads as a tick rather than a tone.
            let t = n as f32 / engine_rate_hz.max(1) as f32;
            let env = (-t / (CLICK_SEC / 5.0)).exp();
            let s = (std::f32::consts::TAU * hz * t).sin() * env * self.volume;
            for ch in 0..channels.min(2) {
                out[frame * channels + ch] += s;
            }
            self.click = Some((n + 1, hz));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RATE_HZ: u32 = 48_000;

    /// Output frames where a click starts (silence, then sound), over `secs`.
    fn onsets(m: &mut Metronome, secs: f64, rate: f64) -> Vec<usize> {
        let block = 256;
        let total = (secs * RATE_HZ as f64) as usize / block * block;
        let mut out = vec![0.0f32; total * 2];
        let mut song = 0.0f64;
        for chunk in out.chunks_mut(block * 2) {
            m.mix(chunk, 2, song.floor() as u64, RATE_HZ, true, rate);
            song += block as f64 * rate;
        }
        let mut starts = Vec::new();
        let mut quiet = true;
        let mut silent_run = 0;
        for (i, f) in out.chunks(2).enumerate() {
            if f[0] != 0.0 {
                if quiet {
                    starts.push(i);
                }
                quiet = false;
                silent_run = 0;
            } else {
                silent_run += 1;
                if silent_run > 100 {
                    quiet = true;
                }
            }
        }
        starts
    }

    #[test]
    fn clicks_on_each_beat() {
        let mut m = Metronome::default();
        m.set(true, 0.5, 120.0, 4);
        // 120 bpm: a beat every half second.
        let starts = onsets(&mut m, 2.1, 1.0);
        assert_eq!(starts.len(), 5, "{starts:?}");
        for (i, s) in starts.iter().enumerate() {
            let expect = i * RATE_HZ as usize / 2;
            assert!(s.abs_diff(expect) <= 1, "beat {i} at {s}, wanted {expect}");
        }
    }

    #[test]
    fn half_speed_spaces_the_clicks_twice_as_far() {
        let mut m = Metronome::default();
        m.set(true, 0.5, 120.0, 4);
        let starts = onsets(&mut m, 2.1, 0.5);
        assert_eq!(starts.len(), 3, "{starts:?}");
        assert!(starts[1].abs_diff(RATE_HZ as usize) <= 2);
    }

    #[test]
    fn silent_when_off_or_stopped() {
        let mut m = Metronome::default();
        assert!(onsets(&mut m, 1.0, 1.0).is_empty());
        m.set(true, 0.5, 120.0, 4);
        let mut out = vec![0.0f32; 512];
        m.mix(&mut out, 2, 0, RATE_HZ, false, 1.0);
        assert!(out.iter().all(|&s| s == 0.0));
    }

    #[test]
    fn the_downbeat_is_pitched_higher() {
        let mut m = Metronome::default();
        m.set(true, 0.5, 120.0, 2);
        let mut out = vec![0.0f32; RATE_HZ as usize * 2];
        m.mix(&mut out, 2, 0, RATE_HZ, true, 1.0);
        // Count zero crossings in the first 10 ms of beat 0 (downbeat) and beat 1.
        let crossings = |from: usize| {
            let w: Vec<f32> = out
                .chunks(2)
                .skip(from + 1)
                .take(480)
                .map(|f| f[0])
                .collect();
            w.windows(2)
                .filter(|p| (p[0] < 0.0) != (p[1] < 0.0))
                .count()
        };
        assert!(crossings(0) > crossings(RATE_HZ as usize / 2) * 3 / 2);
    }
}
