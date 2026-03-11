use midir::{MidiOutput, MidiOutputConnection};

#[derive(Debug, Clone, serde::Serialize)]
pub struct MidiOutputPortInfo {
    pub id: usize,
    pub name: String,
}

/// Persisted selection for a MIDI output port.
///
/// We store both `id` and `name`:
/// - `id` is used for fast selection when it still matches the system's current port list
/// - `name` is used as a fallback if port indices have shifted between runs
///
/// NOTE: This is not currently used by the UI yet (we select by `port_id`),
/// but we keep it for forward-compatibility so we can persist by name.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct MidiOutputSelection {
    pub id: usize,
    pub name: String,
}

/// MIDI clock sender (24 PPQN).
///
/// Clock messages:
/// - 0xF8: timing clock
/// - 0xFA: start
/// - 0xFB: continue
/// - 0xFC: stop
/// - 0xF2: song position pointer (14-bit, in MIDI beats = 16th notes)
///
/// Reference: MIDI 1.0 spec.
pub struct MidiClock {
    conn: MidiOutputConnection,
}

impl MidiClock {
    pub fn open(port_id: usize) -> Result<Self, String> {
        let out = MidiOutput::new("AuralPrimer")
            .map_err(|e| format!("midir: cannot create output: {e}"))?;

        let ports = out.ports();
        if port_id >= ports.len() {
            return Err(format!(
                "invalid midi output port id {port_id} (count={})",
                ports.len()
            ));
        }

        let port = &ports[port_id];
        let name = out
            .port_name(port)
            .unwrap_or_else(|_| "(unknown)".to_string());

        let conn = out
            .connect(port, &format!("AuralPrimer MIDI Clock → {name}"))
            .map_err(|e| format!("midir: connect failed: {e}"))?;

        Ok(Self { conn })
    }

    pub fn send_start(&mut self) -> Result<(), String> {
        self.conn
            .send(&[0xFA])
            .map_err(|e| format!("midir send start: {e}"))
    }

    /// Send MIDI Continue (0xFB).
    ///
    /// Not wired yet; will be used when we implement pause/resume semantics
    /// that distinguish between Stop and Continue.
    pub fn send_continue(&mut self) -> Result<(), String> {
        self.conn
            .send(&[0xFB])
            .map_err(|e| format!("midir send continue: {e}"))
    }

    pub fn send_stop(&mut self) -> Result<(), String> {
        self.conn
            .send(&[0xFC])
            .map_err(|e| format!("midir send stop: {e}"))
    }

    pub fn send_clock(&mut self) -> Result<(), String> {
        self.conn
            .send(&[0xF8])
            .map_err(|e| format!("midir send clock: {e}"))
    }

    /// Send a raw MIDI message.
    ///
    /// Message validation is expected to happen at a higher layer
    /// (service/command boundary) so we keep this as a thin transport call.
    pub fn send_raw(&mut self, bytes: &[u8]) -> Result<(), String> {
        self.conn
            .send(bytes)
            .map_err(|e| format!("midir send raw: {e}"))
    }

    /// Send Song Position Pointer (SPP).
    ///
    /// The value is in MIDI beats (aka "MIDI clocks" groups): 1 MIDI beat = 6 MIDI clocks = 1/16 note.
    ///
    /// We clamp into 14-bit range.
    pub fn send_song_position_pointer(&mut self, midi_beats_16th: u16) -> Result<(), String> {
        let v = midi_beats_16th & 0x3FFF;
        let lsb = (v & 0x7F) as u8;
        let msb = ((v >> 7) & 0x7F) as u8;
        self.conn
            .send(&[0xF2, lsb, msb])
            .map_err(|e| format!("midir send spp: {e}"))
    }
}

pub fn list_midi_output_ports() -> Result<Vec<MidiOutputPortInfo>, String> {
    let out =
        MidiOutput::new("AuralPrimer").map_err(|e| format!("midir: cannot create output: {e}"))?;

    let mut res = Vec::new();
    for (i, p) in out.ports().iter().enumerate() {
        let name = out.port_name(p).unwrap_or_else(|_| "(unknown)".to_string());
        res.push(MidiOutputPortInfo { id: i, name });
    }

    Ok(res)
}

/// Resolve a persisted selection (id+name) to a current port id.
///
/// Not currently used by the UI yet.
pub fn resolve_selection_to_port_id(sel: &MidiOutputSelection) -> Result<usize, String> {
    let out =
        MidiOutput::new("AuralPrimer").map_err(|e| format!("midir: cannot create output: {e}"))?;

    let ports = out.ports();

    // First try by id if still in range and still matches name.
    if sel.id < ports.len() {
        let p = &ports[sel.id];
        let name = out.port_name(p).unwrap_or_else(|_| "(unknown)".to_string());
        if name == sel.name {
            return Ok(sel.id);
        }
    }

    // Fallback: find by name.
    for (i, p) in ports.iter().enumerate() {
        let name = out.port_name(p).unwrap_or_else(|_| "(unknown)".to_string());
        if name == sel.name {
            return Ok(i);
        }
    }

    Err(format!("midi output port not found: {}", sel.name))
}

/// Convert playback seconds to MIDI Song Position Pointer units (16th notes).
///
/// - bpm: quarter-notes per minute
/// - t_sec: playback time in seconds
pub fn seconds_to_spp_16th(t_sec: f64, bpm: f64) -> u16 {
    if !(t_sec.is_finite() && bpm.is_finite() && bpm > 0.0) {
        return 0;
    }

    // quarters = t * bpm/60
    // 16th = quarters * 4
    let v = (t_sec * (bpm / 60.0) * 4.0).floor();
    if v <= 0.0 {
        return 0;
    }

    // MIDI SPP is 14-bit.
    let v_u32 = v as u32;
    let clamped = v_u32.min(0x3FFF);
    clamped as u16
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spp_conversion_120bpm() {
        // 120 bpm = 2 quarter/sec.
        // 1 sec => 2 quarters => 8 sixteenth.
        assert_eq!(seconds_to_spp_16th(1.0, 120.0), 8);

        // 0.5 sec => 1 quarter => 4 sixteenth.
        assert_eq!(seconds_to_spp_16th(0.5, 120.0), 4);
    }

    #[test]
    fn spp_is_clamped_to_14_bit() {
        assert_eq!(seconds_to_spp_16th(1e9, 120.0), 0x3FFF);
    }

    #[test]
    fn spp_packet_bytes_are_7bit() {
        // Just validate our encoding helper behavior.
        // 0x1234 => lsb=0x34, msb=0x24 (because 0x1234 >> 7 == 0x24)
        // We can't construct MidiClock in tests without a MIDI device.
        // So we re-check the bit math matches expected 7-bit packing.
        let v: u16 = 0x1234;
        let lsb = (v & 0x7F) as u8;
        let msb = ((v >> 7) & 0x7F) as u8;
        assert!(lsb < 128);
        assert!(msb < 128);
        assert_eq!((lsb, msb), (0x34, 0x24));
    }
}
