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

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct MidiInputSelection {
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

#[derive(Debug, Clone, Serialize)]
pub struct MidiInputMessageEvent {
    pub timestamp_us: u64,
    pub message_type: String,
    pub status: u8,
    pub channel: Option<u8>,
    pub data1: Option<u8>,
    pub data2: Option<u8>,
    pub value14: Option<u16>,
    pub value_signed: Option<i16>,
    pub bytes: Vec<u8>,
}

pub struct MidiClockInputConnection {
    // Keep alive.
    _conn: MidiInputConnection<()>,
}

pub fn list_midi_input_ports() -> Result<Vec<MidiInputPortInfo>, String> {
    let input =
        MidiInput::new("AuralPrimer").map_err(|e| format!("midir: cannot create input: {e}"))?;
    let mut res = Vec::new();
    for (i, p) in input.ports().iter().enumerate() {
        let name = input
            .port_name(p)
            .unwrap_or_else(|_| "(unknown)".to_string());
        res.push(MidiInputPortInfo { id: i, name });
    }
    Ok(res)
}

pub fn resolve_selection_to_port_id(sel: &MidiInputSelection) -> Result<usize, String> {
    let input =
        MidiInput::new("AuralPrimer").map_err(|e| format!("midir: cannot create input: {e}"))?;

    let ports = input.ports();

    if sel.id < ports.len() {
        let p = &ports[sel.id];
        let name = input
            .port_name(p)
            .unwrap_or_else(|_| "(unknown)".to_string());
        if name == sel.name {
            return Ok(sel.id);
        }
    }

    for (i, p) in ports.iter().enumerate() {
        let name = input
            .port_name(p)
            .unwrap_or_else(|_| "(unknown)".to_string());
        if name == sel.name {
            return Ok(i);
        }
    }

    Err(format!("midi input port not found: {}", sel.name))
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

fn data_bytes_are_7bit(bytes: &[u8]) -> bool {
    bytes.iter().all(|b| *b < 0x80)
}

fn parse_midi_input_message_event(
    timestamp_us: u64,
    msg: &[u8],
    allow_sysex: bool,
) -> Option<MidiInputMessageEvent> {
    if msg.is_empty() {
        return None;
    }
    let status = msg[0];
    if status < 0x80 {
        return None;
    }

    let mut ev = MidiInputMessageEvent {
        timestamp_us,
        message_type: "unknown".to_string(),
        status,
        channel: None,
        data1: None,
        data2: None,
        value14: None,
        value_signed: None,
        bytes: msg.to_vec(),
    };

    match status {
        0x80..=0x8F => {
            if msg.len() != 3 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            ev.message_type = "note_off".to_string();
            ev.channel = Some(status & 0x0F);
            ev.data1 = Some(msg[1]);
            ev.data2 = Some(msg[2]);
        }
        0x90..=0x9F => {
            if msg.len() != 3 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            ev.message_type = if msg[2] == 0 {
                "note_off".to_string()
            } else {
                "note_on".to_string()
            };
            ev.channel = Some(status & 0x0F);
            ev.data1 = Some(msg[1]);
            ev.data2 = Some(msg[2]);
        }
        0xA0..=0xAF => {
            if msg.len() != 3 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            ev.message_type = "poly_aftertouch".to_string();
            ev.channel = Some(status & 0x0F);
            ev.data1 = Some(msg[1]);
            ev.data2 = Some(msg[2]);
        }
        0xB0..=0xBF => {
            if msg.len() != 3 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            ev.message_type = "control_change".to_string();
            ev.channel = Some(status & 0x0F);
            ev.data1 = Some(msg[1]);
            ev.data2 = Some(msg[2]);
        }
        0xC0..=0xCF => {
            if msg.len() != 2 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            ev.message_type = "program_change".to_string();
            ev.channel = Some(status & 0x0F);
            ev.data1 = Some(msg[1]);
        }
        0xD0..=0xDF => {
            if msg.len() != 2 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            ev.message_type = "channel_pressure".to_string();
            ev.channel = Some(status & 0x0F);
            ev.data1 = Some(msg[1]);
        }
        0xE0..=0xEF => {
            if msg.len() != 3 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            let v14 = ((msg[2] as u16) << 7) | (msg[1] as u16);
            ev.message_type = "pitch_bend".to_string();
            ev.channel = Some(status & 0x0F);
            ev.data1 = Some(msg[1]);
            ev.data2 = Some(msg[2]);
            ev.value14 = Some(v14);
            ev.value_signed = Some(v14 as i16 - 8192);
        }
        0xF0 => {
            if !allow_sysex {
                return None;
            }
            if msg.len() < 2 || *msg.last().unwrap_or(&0x00) != 0xF7 {
                return None;
            }
            if !data_bytes_are_7bit(&msg[1..msg.len() - 1]) {
                return None;
            }
            ev.message_type = "sysex".to_string();
        }
        0xF1 => {
            if msg.len() != 2 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            ev.message_type = "time_code_quarter_frame".to_string();
            ev.data1 = Some(msg[1]);
        }
        0xF2 => {
            if msg.len() != 3 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            let v14 = ((msg[2] as u16) << 7) | (msg[1] as u16);
            ev.message_type = "song_position_pointer".to_string();
            ev.data1 = Some(msg[1]);
            ev.data2 = Some(msg[2]);
            ev.value14 = Some(v14);
        }
        0xF3 => {
            if msg.len() != 2 || !data_bytes_are_7bit(&msg[1..]) {
                return None;
            }
            ev.message_type = "song_select".to_string();
            ev.data1 = Some(msg[1]);
        }
        0xF6 => {
            if msg.len() != 1 {
                return None;
            }
            ev.message_type = "tune_request".to_string();
        }
        0xF8 => {
            if msg.len() != 1 {
                return None;
            }
            ev.message_type = "clock".to_string();
        }
        0xFA => {
            if msg.len() != 1 {
                return None;
            }
            ev.message_type = "start".to_string();
        }
        0xFB => {
            if msg.len() != 1 {
                return None;
            }
            ev.message_type = "continue".to_string();
        }
        0xFC => {
            if msg.len() != 1 {
                return None;
            }
            ev.message_type = "stop".to_string();
        }
        0xF9 | 0xFD | 0xFE | 0xFF => {
            if msg.len() != 1 {
                return None;
            }
            ev.message_type = "system_realtime".to_string();
        }
        _ => {
            // Keep unknown status as pass-through payload for debugging/integration.
            ev.message_type = "unknown".to_string();
        }
    }

    Some(ev)
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
    allow_sysex: bool,
) -> Result<MidiClockInputConnection, String> {
    let mut input =
        MidiInput::new("AuralPrimer").map_err(|e| format!("midir: cannot create input: {e}"))?;

    // We need realtime/system transport bytes; SysEx is opt-in.
    if allow_sysex {
        input.ignore(Ignore::None);
    } else {
        input.ignore(Ignore::Sysex);
    }

    let ports = input.ports();
    if port_id >= ports.len() {
        return Err(format!(
            "invalid midi input port id {port_id} (count={})",
            ports.len()
        ));
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
            move |ts, msg, _| {
                // Keep handler small; all operations are best-effort.
                let now = Instant::now();

                if let Some(ev) = parse_midi_input_message_event(ts, msg, allow_sysex) {
                    let _ = app.emit("midi_input_message", ev);
                }

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
                            let bpm_for_seek = s.estimate_bpm().unwrap_or(120.0) * s.tempo_scale;
                            let t_sec = spp_16th_to_seconds(spp, bpm_for_seek);
                            let _ = app.emit("midi_clock_seek", MidiClockSeekEvent { t_sec });
                        }
                    }
                    _ => {
                        // non-clock MIDI messages are surfaced via midi_input_message.
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

    #[test]
    fn parses_note_on_and_note_off() {
        let on = parse_midi_input_message_event(123, &[0x90, 60, 100], false).unwrap();
        assert_eq!(on.message_type, "note_on");
        assert_eq!(on.channel, Some(0));
        assert_eq!(on.data1, Some(60));
        assert_eq!(on.data2, Some(100));

        let off = parse_midi_input_message_event(124, &[0x90, 60, 0], false).unwrap();
        assert_eq!(off.message_type, "note_off");
    }

    #[test]
    fn parses_pitch_bend_signed_value() {
        // center => 8192 => signed 0
        let center = parse_midi_input_message_event(1, &[0xE0, 0x00, 0x40], false).unwrap();
        assert_eq!(center.message_type, "pitch_bend");
        assert_eq!(center.value14, Some(8192));
        assert_eq!(center.value_signed, Some(0));

        let down = parse_midi_input_message_event(1, &[0xE1, 0x00, 0x00], false).unwrap();
        assert_eq!(down.value_signed, Some(-8192));
    }

    #[test]
    fn sysex_requires_opt_in() {
        let msg = [0xF0, 0x7D, 0x10, 0x20, 0xF7];
        assert!(parse_midi_input_message_event(1, &msg, false).is_none());
        let ev = parse_midi_input_message_event(1, &msg, true).unwrap();
        assert_eq!(ev.message_type, "sysex");
    }

    #[test]
    fn rejects_non_7bit_data_bytes() {
        assert!(parse_midi_input_message_event(1, &[0x90, 0x80, 0x01], false).is_none());
        assert!(parse_midi_input_message_event(1, &[0xB0, 1, 0xFF], false).is_none());
    }
}
