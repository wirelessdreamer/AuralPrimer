use crate::midi_clock::{seconds_to_spp_16th, MidiClock};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub enum MidiClockCommand {
    /// Open (or re-open) a MIDI output connection using the given port id.
    SelectPort { port_id: usize },
    /// Set tempo for scheduling (quarter notes per minute).
    SetBpm { bpm: f64 },
    /// Seek to an absolute playback time (seconds). Sends SPP best-effort.
    Seek { t_sec: f64 },
    /// Start clock; if already running, no-op.
    Start,
    /// Continue clock; if already running, no-op.
    ///
    /// Use this to resume after a Stop without resetting the song position.
    Continue,
    /// Stop clock; if already stopped, no-op.
    Stop,
    /// Enable/disable SysEx pass-through for outbound raw MIDI messages.
    SetAllowSysEx { enabled: bool },
    /// Send a validated raw MIDI message on the selected output port.
    SendRaw { bytes: Vec<u8> },
    /// Send CC123 (all notes off), either on one channel or all channels.
    AllNotesOff { channel: Option<u8> },
    /// Shut down the thread.
    Shutdown,
}

/// Background MIDI clock sender.
///
/// Guarantees:
/// - Best-effort timing; not hard realtime.
/// - Sends SPP on Seek and also right before Start.
/// - Sends Start when transitioning stopped->running.
pub struct MidiClockService {
    tx: Sender<MidiClockCommand>,
    handle: Option<JoinHandle<()>>,
}

impl MidiClockService {
    pub fn spawn() -> Self {
        let (tx, rx) = mpsc::channel::<MidiClockCommand>();
        let handle = thread::spawn(move || run_loop(rx));
        Self {
            tx,
            handle: Some(handle),
        }
    }

    pub fn send(&self, cmd: MidiClockCommand) {
        // best-effort; if receiver died, ignore.
        let _ = self.tx.send(cmd);
    }

    /// Gracefully stop the thread and wait for it.
    pub fn shutdown(mut self) {
        let _ = self.tx.send(MidiClockCommand::Shutdown);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

fn run_loop(rx: Receiver<MidiClockCommand>) {
    let mut clock: Option<MidiClock> = None;
    let mut bpm: f64 = 120.0;
    let mut t_sec: f64 = 0.0;
    let mut running = false;
    let mut allow_sysex = false;

    // scheduling
    let mut next_tick = Instant::now();

    loop {
        // process queued commands
        while let Ok(cmd) = rx.try_recv() {
            match cmd {
                MidiClockCommand::SelectPort { port_id } => {
                    clock = MidiClock::open(port_id).ok();
                }
                MidiClockCommand::SetBpm { bpm: b } => {
                    if b.is_finite() && b > 0.0 {
                        bpm = b;
                    }
                }
                MidiClockCommand::Seek { t_sec: t } => {
                    if t.is_finite() && t >= 0.0 {
                        t_sec = t;
                    }
                    if let Some(c) = clock.as_mut() {
                        let spp = seconds_to_spp_16th(t_sec, bpm);
                        let _ = c.send_song_position_pointer(spp);
                    }
                }
                MidiClockCommand::Start => {
                    if !running {
                        if let Some(c) = clock.as_mut() {
                            // SPP first (best-effort)
                            let spp = seconds_to_spp_16th(t_sec, bpm);
                            let _ = c.send_song_position_pointer(spp);
                            let _ = c.send_start();
                        }
                        running = true;
                        next_tick = Instant::now();
                    }
                }
                MidiClockCommand::Continue => {
                    if !running {
                        if let Some(c) = clock.as_mut() {
                            let _ = c.send_continue();
                        }
                        running = true;
                        next_tick = Instant::now();
                    }
                }
                MidiClockCommand::Stop => {
                    if running {
                        if let Some(c) = clock.as_mut() {
                            let _ = c.send_stop();
                        }
                        running = false;
                    }
                }
                MidiClockCommand::SetAllowSysEx { enabled } => {
                    allow_sysex = enabled;
                }
                MidiClockCommand::SendRaw { bytes } => {
                    if let Ok(msg) = sanitize_outbound_midi_message(&bytes, allow_sysex) {
                        if let Some(c) = clock.as_mut() {
                            let _ = c.send_raw(&msg);
                        }
                    }
                }
                MidiClockCommand::AllNotesOff { channel } => {
                    if let Some(c) = clock.as_mut() {
                        for msg in all_notes_off_messages(channel) {
                            let _ = c.send_raw(&msg);
                        }
                    }
                }
                MidiClockCommand::Shutdown => return,
            }
        }

        if !running {
            // Sleep a bit while idle.
            thread::sleep(Duration::from_millis(10));
            continue;
        }

        // tick at 24 PPQN
        let period = tick_period_for_bpm(bpm);

        let now = Instant::now();
        if now >= next_tick {
            if let Some(c) = clock.as_mut() {
                let _ = c.send_clock();
            }
            next_tick += period;
        } else {
            // sleep until next tick (cap)
            let wait = (next_tick - now).min(Duration::from_millis(10));
            thread::sleep(wait);
        }
    }
}

fn tick_period_for_bpm(bpm: f64) -> Duration {
    if !(bpm.is_finite() && bpm > 0.0) {
        return Duration::from_secs_f64(60.0 / (120.0 * 24.0));
    }
    let period_sec = 60.0 / (bpm * 24.0);
    Duration::from_secs_f64(period_sec.max(1e-4))
}

fn data_bytes_are_7bit(bytes: &[u8]) -> bool {
    bytes.iter().all(|b| *b < 0x80)
}

/// Validate outbound raw MIDI message shape and SysEx policy.
///
/// We intentionally keep this strict:
/// - no running-status encoding
/// - exact message lengths for non-SysEx statuses
/// - data bytes must be 7-bit
fn sanitize_outbound_midi_message(bytes: &[u8], allow_sysex: bool) -> Result<Vec<u8>, String> {
    if bytes.is_empty() {
        return Err("empty MIDI message".to_string());
    }

    let status = bytes[0];
    if status < 0x80 {
        return Err("MIDI message must start with a status byte".to_string());
    }

    match status {
        0x80..=0x8F | 0x90..=0x9F | 0xA0..=0xAF | 0xB0..=0xBF | 0xE0..=0xEF => {
            if bytes.len() != 3 {
                return Err("expected 3-byte channel voice message".to_string());
            }
            if !data_bytes_are_7bit(&bytes[1..]) {
                return Err("channel voice data bytes must be 7-bit".to_string());
            }
        }
        0xC0..=0xCF | 0xD0..=0xDF => {
            if bytes.len() != 2 {
                return Err("expected 2-byte channel voice message".to_string());
            }
            if !data_bytes_are_7bit(&bytes[1..]) {
                return Err("channel voice data byte must be 7-bit".to_string());
            }
        }
        0xF0 => {
            if !allow_sysex {
                return Err("SysEx is disabled for this output".to_string());
            }
            if bytes.len() < 2 || *bytes.last().unwrap_or(&0x00) != 0xF7 {
                return Err("SysEx message must start with F0 and end with F7".to_string());
            }
            if !data_bytes_are_7bit(&bytes[1..bytes.len() - 1]) {
                return Err("SysEx payload bytes must be 7-bit".to_string());
            }
        }
        0xF1 | 0xF3 => {
            if bytes.len() != 2 || !data_bytes_are_7bit(&bytes[1..]) {
                return Err("system common message has invalid shape".to_string());
            }
        }
        0xF2 => {
            if bytes.len() != 3 || !data_bytes_are_7bit(&bytes[1..]) {
                return Err("song position pointer has invalid shape".to_string());
            }
        }
        0xF6 | 0xF8..=0xFF => {
            if bytes.len() != 1 {
                return Err("single-byte system message has invalid length".to_string());
            }
        }
        _ => {
            return Err(format!("unsupported status byte 0x{status:02X}"));
        }
    }

    Ok(bytes.to_vec())
}

fn all_notes_off_messages(channel: Option<u8>) -> Vec<Vec<u8>> {
    if let Some(ch) = channel {
        let c = ch.min(15);
        return vec![vec![0xB0 | c, 123, 0]];
    }

    let mut msgs = Vec::with_capacity(16);
    for c in 0u8..16 {
        msgs.push(vec![0xB0 | c, 123, 0]);
    }
    msgs
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tick_period_uses_default_for_invalid_bpm() {
        let p_nan = tick_period_for_bpm(f64::NAN);
        let p_neg = tick_period_for_bpm(-1.0);
        let p_def = tick_period_for_bpm(120.0);
        assert_eq!(p_nan, p_def);
        assert_eq!(p_neg, p_def);
    }

    #[test]
    fn tick_period_scales_with_bpm() {
        let p120 = tick_period_for_bpm(120.0);
        let p60 = tick_period_for_bpm(60.0);
        assert!(p60 > p120);
    }

    #[test]
    fn sanitize_rejects_invalid_status_prefix() {
        let err = sanitize_outbound_midi_message(&[0x40, 0x10], false).unwrap_err();
        assert!(err.contains("status byte"));
    }

    #[test]
    fn sanitize_accepts_channel_messages() {
        let on = sanitize_outbound_midi_message(&[0x90, 60, 100], false).unwrap();
        let bend = sanitize_outbound_midi_message(&[0xE2, 0x00, 0x40], false).unwrap();
        assert_eq!(on, vec![0x90, 60, 100]);
        assert_eq!(bend, vec![0xE2, 0x00, 0x40]);
    }

    #[test]
    fn sanitize_sysex_policy_is_enforced() {
        let msg = [0xF0, 0x7D, 0x01, 0x02, 0xF7];
        assert!(sanitize_outbound_midi_message(&msg, false).is_err());
        assert!(sanitize_outbound_midi_message(&msg, true).is_ok());
    }

    #[test]
    fn all_notes_off_builds_expected_count() {
        let all = all_notes_off_messages(None);
        assert_eq!(all.len(), 16);
        assert_eq!(all[0], vec![0xB0, 123, 0]);
        assert_eq!(all[15], vec![0xBF, 123, 0]);

        let single = all_notes_off_messages(Some(5));
        assert_eq!(single, vec![vec![0xB5, 123, 0]]);
    }
}
