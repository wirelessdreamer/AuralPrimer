use midir::{Ignore, MidiInput, MidiInputConnection};
use serde::Serialize;
use std::sync::{Arc, Mutex};
use std::time::Instant;
use tauri::{AppHandle, Emitter};

#[derive(Debug, Clone, Serialize)]
pub struct MidiInputPortInfo {
    pub id: usize,
    pub name: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct MidiClockBpmEvent {
    pub bpm: f64,
    pub raw_bpm: f64,
    pub tempo_scale: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct MidiClockSeekEvent {
    pub t_sec: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct MidiClockTickEvent {
    /// Seconds of song time to advance (already scaled by tempo_scale).
    pub dt_sec: f64,
}

pub struct MidiClockInputConnection {
    // Keep alive.
    _conn: MidiInputConnection<()>,
}

pub fn list_midi_input_ports() -> Result<Vec<MidiInputPortInfo>, String> {
    let input = MidiInput::new("AuralPrimer").map_err(|e| format!("midir: cannot create input: {e}"))?;
    let mut res = Vec::new();
    for (i, p) in input.ports().iter().enumerate() {
        let name = input
            .port_name(p)
            .unwrap_or_else(|_| "(unknown)".to_string());
        res.push(MidiInputPortInfo { id: i, name });
    }
    Ok(res)
}

fn parse_spp_16th(msg: &[u8]) -> Option<u16> {
    if msg.len() != 3 {
        return None;
    }
    if msg[0] != 0xF2 {
        return None;
    }
    let lsb = msg[1] & 0x7F;
    let msb = msg[2] & 0x7F;
    Some(((msb as u16) << 7) | (lsb as u16))
}

fn spp_16th_to_seconds(spp_16th: u16, bpm: f64) -> f64 {
    if !(bpm.is_finite() && bpm > 0.0) {
        return 0.0;
    }
    // spp units are 16th notes.
    // quarters = 16th / 4
    // seconds = quarters * 60 / bpm
    (spp_16th as f64 / 4.0) * (60.0 / bpm)
}

#[derive(Debug)]
struct ClockState {
    tempo_scale: f64,
    running: bool,
    // last few tick instants for bpm estimation
    last_tick: Option<Instant>,
    // simple EMA of tick duration
    tick_dt_ema: Option<f64>,
    ticks_since_emit: u32,
}

impl ClockState {
    fn new(tempo_scale: f64) -> Self {
        Self {
            tempo_scale,
            running: false,
            last_tick: None,
            tick_dt_ema: None,
            ticks_since_emit: 0,
        }
    }

    fn estimate_bpm(&self) -> Option<f64> {
        let dt = self.tick_dt_ema?;
        if !(dt.is_finite() && dt > 0.0) {
            return None;
        }
        // dt is seconds per MIDI clock tick (24 ticks per quarter note)
        let raw = 60.0 / (dt * 24.0);
        if !(raw.is_finite() && raw > 0.0) {
            return None;
        }
        Some(raw)
    }
}

/// Start listening to MIDI clock on a selected input port.
///
/// Emits Tauri events:
/// - `midi_clock_start`
/// - `midi_clock_stop`
/// - `midi_clock_bpm` (payload: MidiClockBpmEvent)
/// - `midi_clock_seek` (payload: MidiClockSeekEvent) when SPP received
pub fn start_midi_clock_input(
    app: AppHandle,
    port_id: usize,
    tempo_scale: f64,
) -> Result<MidiClockInputConnection, String> {
    let mut input = MidiInput::new("AuralPrimer").map_err(|e| format!("midir: cannot create input: {e}"))?;

    // We do want to receive clock + transport + SPP; ignore sysex for now.
    input.ignore(Ignore::None);

    let ports = input.ports();
    if port_id >= ports.len() {
        return Err(format!("invalid midi input port id {port_id} (count={})", ports.len()));
    }

    let port = &ports[port_id];
    let name = input
        .port_name(port)
        .unwrap_or_else(|_| "(unknown)".to_string());

    let scale = if tempo_scale.is_finite() && tempo_scale > 0.0 {
        tempo_scale
    } else {
        1.0
    };

    let st = Arc::new(Mutex::new(ClockState::new(scale)));

    let conn = input
        .connect(
            port,
            &format!("AuralPrimer MIDI Clock In - {name}"),
            move |_ts, msg, _| {
                // Keep handler small; all operations are best-effort.
                let now = Instant::now();
                let mut s = st.lock().unwrap();

                match msg {
                    // Clock tick
                    [0xF8] => {
                        if let Some(last) = s.last_tick {
                            let dt_wall = (now - last).as_secs_f64();
                            // EMA to smooth jitter; alpha chosen to respond within ~1 beat.
                            let alpha = 0.2;
                            s.tick_dt_ema = Some(match s.tick_dt_ema {
                                None => dt_wall,
                                Some(prev) => prev * (1.0 - alpha) + dt_wall * alpha,
                            });

                            // Only advance transport when a start/continue has been seen.
                            if s.running {
                                // Scale song time relative to wall time.
                                let dt_sec = dt_wall * s.tempo_scale;
                                // Clamp extreme gaps (device unplugged / app paused) to avoid huge jumps.
                                let dt_sec = dt_sec.clamp(0.0, 0.25);
                                let _ = app.emit("midi_clock_tick", MidiClockTickEvent { dt_sec });
                            }
                        }
                        s.last_tick = Some(now);
                        s.ticks_since_emit += 1;

                        // Emit BPM about twice per beat (12 ticks = 1/2 beat).
                        if s.ticks_since_emit >= 12 {
                            s.ticks_since_emit = 0;
                            if let Some(raw_bpm) = s.estimate_bpm() {
                                let bpm = raw_bpm * s.tempo_scale;
                                let _ = app.emit(
                                    "midi_clock_bpm",
                                    MidiClockBpmEvent {
                                        bpm,
                                        raw_bpm,
                                        tempo_scale: s.tempo_scale,
                                    },
                                );
                            }
                        }
                    }

                    // Start
                    [0xFA] => {
                        s.running = true;
                        s.last_tick = None;
                        s.tick_dt_ema = None;
                        s.ticks_since_emit = 0;
                        let _ = app.emit("midi_clock_start", ());
                    }
                    // Continue
                    [0xFB] => {
                        s.running = true;
                        let _ = app.emit("midi_clock_start", ());
                    }
                    // Stop
                    [0xFC] => {
                        s.running = false;
                        let _ = app.emit("midi_clock_stop", ());
                    }

                    // Song Position Pointer
                    _ if msg.first() == Some(&0xF2) => {
                        if let Some(spp) = parse_spp_16th(msg) {
                            let bpm_for_seek = s
                                .estimate_bpm()
                                .unwrap_or(120.0)
                                * s.tempo_scale;
                            let t_sec = spp_16th_to_seconds(spp, bpm_for_seek);
                            let _ = app.emit("midi_clock_seek", MidiClockSeekEvent { t_sec });
                        }
                    }
                    _ => {
                        // ignore (note on/off etc. will be added later)
                    }
                }
            },
            (),
        )
        .map_err(|e| format!("midir: connect failed: {e}"))?;

    Ok(MidiClockInputConnection { _conn: conn })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spp_parse() {
        // 0x1234 => lsb=0x34, msb=0x24 (7-bit)
        let v = parse_spp_16th(&[0xF2, 0x34, 0x24]).unwrap();
        assert_eq!(v, 0x1234);
    }

    #[test]
    fn spp_to_seconds_120() {
        // 120 bpm = 2 quarters/sec.
        // spp=8 (8 16ths) = 2 quarters => 1 sec
        assert!((spp_16th_to_seconds(8, 120.0) - 1.0).abs() < 1e-9);
    }
}
