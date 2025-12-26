/// Native audio engine scaffolding.
///
/// Phase 0 goal: define sample-accurate transport primitives in Rust
/// with deterministic unit tests, before wiring any actual audio I/O.

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LoopRegion {
    /// Inclusive loop start in frames.
    pub start_frame: u64,
    /// Exclusive loop end in frames. Must be > start_frame.
    pub end_frame: u64,
}

impl LoopRegion {
    pub fn new(start_frame: u64, end_frame: u64) -> Result<Self, String> {
        if end_frame <= start_frame {
            return Err("loop end must be > start".to_string());
        }
        Ok(Self {
            start_frame,
            end_frame,
        })
    }
}

#[derive(Debug, Clone)]
pub struct Transport {
    sample_rate_hz: u32,
    position_frames: u64,
    is_playing: bool,
    loop_region: Option<LoopRegion>,
}

impl Transport {
    pub fn new(sample_rate_hz: u32) -> Result<Self, String> {
        if sample_rate_hz == 0 {
            return Err("sample_rate_hz must be > 0".to_string());
        }
        Ok(Self {
            sample_rate_hz,
            position_frames: 0,
            is_playing: false,
            loop_region: None,
        })
    }

    pub fn sample_rate_hz(&self) -> u32 {
        self.sample_rate_hz
    }

    pub fn position_frames(&self) -> u64 {
        self.position_frames
    }

    pub fn position_seconds(&self) -> f64 {
        self.position_frames as f64 / self.sample_rate_hz as f64
    }

    pub fn is_playing(&self) -> bool {
        self.is_playing
    }

    pub fn loop_region(&self) -> Option<LoopRegion> {
        self.loop_region
    }

    pub fn set_playing(&mut self, playing: bool) {
        self.is_playing = playing;
    }

    pub fn seek_seconds(&mut self, t_sec: f64) {
        self.position_frames = seconds_to_frames_clamped(t_sec, self.sample_rate_hz);
        self.apply_loop_invariant();
    }

    pub fn seek_frames(&mut self, frame: u64) {
        self.position_frames = frame;
        self.apply_loop_invariant();
    }

    pub fn set_loop_region(&mut self, loop_region: Option<LoopRegion>) {
        self.loop_region = loop_region;
        self.apply_loop_invariant();
    }

    /// Advance the transport by `frames` frames.
    ///
    /// If `is_playing` is false, this is a no-op.
    pub fn advance_frames(&mut self, frames: u64) {
        if !self.is_playing {
            return;
        }

        if frames == 0 {
            return;
        }

        let Some(lr) = self.loop_region else {
            self.position_frames = self.position_frames.saturating_add(frames);
            return;
        };

        // Looping behavior:
        // - position is always kept within [start, end)
        // - advancing wraps around when crossing end
        let loop_len = lr.end_frame - lr.start_frame;

        // If loop_len is 0, LoopRegion::new would have rejected it.
        let rel = if self.position_frames < lr.start_frame {
            0
        } else {
            (self.position_frames - lr.start_frame) % loop_len
        };

        let rel2 = (rel + (frames % loop_len)) % loop_len;
        self.position_frames = lr.start_frame + rel2;
    }

    fn apply_loop_invariant(&mut self) {
        let Some(lr) = self.loop_region else {
            return;
        };

        // Clamp into the loop range.
        if self.position_frames < lr.start_frame {
            self.position_frames = lr.start_frame;
        }
        if self.position_frames >= lr.end_frame {
            // Put it at the loop start (common DAW behavior).
            self.position_frames = lr.start_frame;
        }
    }
}

fn seconds_to_frames_clamped(t_sec: f64, sample_rate_hz: u32) -> u64 {
    if !t_sec.is_finite() || t_sec <= 0.0 {
        return 0;
    }
    // Clamp very large values safely.
    let frames_f64 = t_sec * sample_rate_hz as f64;
    if frames_f64 >= u64::MAX as f64 {
        return u64::MAX;
    }
    frames_f64.floor() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seconds_to_frames_clamped_handles_negative_nan_inf() {
        assert_eq!(seconds_to_frames_clamped(-1.0, 48_000), 0);
        assert_eq!(seconds_to_frames_clamped(f64::NAN, 48_000), 0);
        // Non-finite times are treated as invalid and clamped to 0.
        assert_eq!(seconds_to_frames_clamped(f64::INFINITY, 48_000), 0);
    }

    #[test]
    fn transport_advance_no_loop_stops_when_not_playing() {
        let mut t = Transport::new(48_000).unwrap();
        t.seek_frames(100);
        t.advance_frames(50);
        assert_eq!(t.position_frames(), 100);
    }

    #[test]
    fn transport_advance_no_loop_advances_when_playing() {
        let mut t = Transport::new(48_000).unwrap();
        t.set_playing(true);
        t.seek_frames(100);
        t.advance_frames(50);
        assert_eq!(t.position_frames(), 150);
    }

    #[test]
    fn loop_region_constructor_rejects_invalid() {
        assert!(LoopRegion::new(10, 10).is_err());
        assert!(LoopRegion::new(10, 9).is_err());
    }

    #[test]
    fn transport_loop_invariant_clamps_seek_below_start() {
        let mut t = Transport::new(48_000).unwrap();
        t.set_loop_region(Some(LoopRegion::new(100, 200).unwrap()));
        t.seek_frames(0);
        assert_eq!(t.position_frames(), 100);
    }

    #[test]
    fn transport_loop_invariant_wraps_seek_at_or_after_end_to_start() {
        let mut t = Transport::new(48_000).unwrap();
        t.set_loop_region(Some(LoopRegion::new(100, 200).unwrap()));
        t.seek_frames(200);
        assert_eq!(t.position_frames(), 100);
        t.seek_frames(250);
        assert_eq!(t.position_frames(), 100);
    }

    #[test]
    fn transport_advance_with_loop_wraps() {
        let mut t = Transport::new(48_000).unwrap();
        t.set_loop_region(Some(LoopRegion::new(100, 200).unwrap()));
        t.set_playing(true);
        t.seek_frames(190);
        t.advance_frames(15);
        // Loop length is 100 frames (100..200). 190 + 15 => 205 => wraps to 105.
        assert_eq!(t.position_frames(), 105);
    }

    #[test]
    fn transport_advance_with_loop_handles_huge_steps_modulo_loop_len() {
        let mut t = Transport::new(48_000).unwrap();
        t.set_loop_region(Some(LoopRegion::new(100, 200).unwrap()));
        t.set_playing(true);
        t.seek_frames(100);
        t.advance_frames(1_000_000_123);
        // Loop length 100. 1_000_000_123 % 100 = 23.
        assert_eq!(t.position_frames(), 123);
    }
}
