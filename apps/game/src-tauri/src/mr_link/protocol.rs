//! Wire encoding for the MR link, per `docs/mr-link-protocol.md`.
//!
//! Kept free of I/O so every byte of the format is unit-testable. The C# client
//! parses these by hand, so the tests below pin exact byte layouts: a silent
//! reshaping here would break the headset with no compile error anywhere.

use std::time::Instant;

/// TCP frame types.
pub mod frame {
    pub const HELLO: u8 = 0x01;
    pub const WELCOME: u8 = 0x02;
    pub const CHART: u8 = 0x10;
    pub const SONG_CHANGED: u8 = 0x11;
    pub const PING: u8 = 0x20;
    pub const PONG: u8 = 0x21;
    pub const TRANSPORT: u8 = 0x30;
}

/// UDP datagram tags.
pub mod datagram {
    pub const POSITION: u8 = 0x40;
    pub const NOTES: u8 = 0x41;
}

/// Payload ceiling. A larger prefix is treated as a protocol error and the
/// connection dropped, rather than trusted into an allocation.
pub const MAX_PAYLOAD: usize = 16 * 1024 * 1024;

pub const PROTOCOL_VERSION: u32 = 1;

/// Monotonic microseconds since process start.
///
/// The absolute origin is irrelevant — the client only ever takes differences
/// and an offset — but it must be monotonic, so this is deliberately not
/// derived from the wall clock, which can step backwards.
pub fn host_clock_us() -> u64 {
    use std::sync::OnceLock;
    static ORIGIN: OnceLock<Instant> = OnceLock::new();
    ORIGIN.get_or_init(Instant::now).elapsed().as_micros() as u64
}

/// Encode a length-prefixed frame: `len: u32 | type: u8 | payload`.
///
/// `len` counts the payload only.
pub fn encode_frame(frame_type: u8, payload: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(5 + payload.len());
    out.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    out.push(frame_type);
    out.extend_from_slice(payload);
    out
}

/// A decoded frame header.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FrameHeader {
    pub len: usize,
    pub frame_type: u8,
}

/// Decode the 5-byte header. `None` if the declared length is implausible.
pub fn decode_frame_header(bytes: &[u8; 5]) -> Option<FrameHeader> {
    let len = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]) as usize;
    if len > MAX_PAYLOAD {
        return None;
    }
    Some(FrameHeader {
        len,
        frame_type: bytes[4],
    })
}

/// A song-position sample. `host_clock_us` is the instant `song_time_sec` was
/// read — the two are only meaningful together, which is why they travel as one
/// struct rather than as separate fields anyone could sample apart.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PositionSample {
    pub song_time_sec: f64,
    pub host_clock_us: u64,
    pub playing: bool,
}

impl PositionSample {
    pub const ENCODED_LEN: usize = 18;

    pub fn encode(&self) -> [u8; Self::ENCODED_LEN] {
        let mut out = [0u8; Self::ENCODED_LEN];
        out[0] = datagram::POSITION;
        out[1..9].copy_from_slice(&self.song_time_sec.to_le_bytes());
        out[9..17].copy_from_slice(&self.host_clock_us.to_le_bytes());
        out[17] = u8::from(self.playing);
        out
    }

    pub fn decode(bytes: &[u8]) -> Option<Self> {
        if bytes.len() != Self::ENCODED_LEN || bytes[0] != datagram::POSITION {
            return None;
        }
        Some(Self {
            song_time_sec: f64::from_le_bytes(bytes[1..9].try_into().ok()?),
            host_clock_us: u64::from_le_bytes(bytes[9..17].try_into().ok()?),
            playing: bytes[17] & 1 != 0,
        })
    }
}

/// The complete set of currently-held notes.
///
/// Full state rather than deltas, deliberately: a dropped note-off would leave
/// a key lit indefinitely, whereas a dropped snapshot is corrected by the next
/// one. 128 pitches is the hard ceiling and fits a datagram trivially.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct NoteState {
    pub host_clock_us: u64,
    /// `(pitch, velocity 0..127)`, ascending by pitch.
    pub held: Vec<(u8, u8)>,
}

impl NoteState {
    pub fn encode(&self) -> Vec<u8> {
        // Truncate rather than overflow the count byte. 128 simultaneous notes
        // is already beyond any real instrument.
        let held = &self.held[..self.held.len().min(128)];
        let mut out = Vec::with_capacity(10 + held.len() * 2);
        out.push(datagram::NOTES);
        out.extend_from_slice(&self.host_clock_us.to_le_bytes());
        out.push(held.len() as u8);
        for (pitch, vel) in held {
            out.push(*pitch);
            out.push(*vel);
        }
        out
    }

    pub fn decode(bytes: &[u8]) -> Option<Self> {
        if bytes.len() < 10 || bytes[0] != datagram::NOTES {
            return None;
        }
        let host_clock_us = u64::from_le_bytes(bytes[1..9].try_into().ok()?);
        let count = bytes[9] as usize;
        // Reject a truncated tail rather than returning a partial chord: a
        // half-decoded snapshot would light the wrong keys.
        if bytes.len() != 10 + count * 2 {
            return None;
        }
        let held = bytes[10..]
            .chunks_exact(2)
            .map(|c| (c[0], c[1]))
            .collect();
        Some(Self {
            host_clock_us,
            held,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_header_round_trips() {
        let framed = encode_frame(frame::CHART, b"{}");
        assert_eq!(framed.len(), 7);
        let header = decode_frame_header(&framed[..5].try_into().unwrap()).unwrap();
        assert_eq!(header.frame_type, frame::CHART);
        assert_eq!(header.len, 2);
        assert_eq!(&framed[5..], b"{}");
    }

    #[test]
    fn frame_length_is_little_endian_and_excludes_the_type_byte() {
        // Pinned: the C# reader takes the length as payload-only LE. Getting
        // either wrong desynchronises the stream permanently rather than
        // failing loudly.
        let framed = encode_frame(frame::HELLO, &[0xAA; 258]);
        assert_eq!(&framed[..4], &[0x02, 0x01, 0x00, 0x00]);
        assert_eq!(framed[4], frame::HELLO);
        assert_eq!(framed.len(), 5 + 258);
    }

    #[test]
    fn empty_payload_is_valid() {
        let framed = encode_frame(frame::PING, &[]);
        assert_eq!(framed.len(), 5);
        let header = decode_frame_header(&framed[..5].try_into().unwrap()).unwrap();
        assert_eq!(header.len, 0);
    }

    #[test]
    fn rejects_an_implausible_length_prefix() {
        // Refuse to allocate on a corrupt or hostile prefix.
        let mut bytes = [0u8; 5];
        bytes[..4].copy_from_slice(&(MAX_PAYLOAD as u32 + 1).to_le_bytes());
        assert_eq!(decode_frame_header(&bytes), None);
    }

    #[test]
    fn position_round_trips_and_is_exactly_18_bytes() {
        let sample = PositionSample {
            song_time_sec: 12.5,
            host_clock_us: 987_654_321,
            playing: true,
        };
        let bytes = sample.encode();
        assert_eq!(bytes.len(), 18);
        assert_eq!(bytes[0], datagram::POSITION);
        assert_eq!(PositionSample::decode(&bytes), Some(sample));
    }

    #[test]
    fn position_carries_the_paused_flag() {
        let paused = PositionSample {
            song_time_sec: 3.0,
            host_clock_us: 1,
            playing: false,
        };
        assert_eq!(PositionSample::decode(&paused.encode()), Some(paused));
    }

    #[test]
    fn position_rejects_wrong_length_or_tag() {
        let good = PositionSample {
            song_time_sec: 1.0,
            host_clock_us: 2,
            playing: true,
        }
        .encode();
        assert_eq!(PositionSample::decode(&good[..17]), None);
        let mut wrong_tag = good;
        wrong_tag[0] = datagram::NOTES;
        assert_eq!(PositionSample::decode(&wrong_tag), None);
    }

    #[test]
    fn notes_round_trip_including_the_empty_set() {
        // Empty matters: it is how "all keys released" is communicated, and it
        // must survive the round trip rather than being mistaken for a
        // malformed packet.
        for held in [vec![], vec![(60, 100)], vec![(60, 100), (64, 90), (67, 80)]] {
            let state = NoteState {
                host_clock_us: 42,
                held,
            };
            assert_eq!(NoteState::decode(&state.encode()), Some(state));
        }
    }

    #[test]
    fn notes_reject_a_truncated_tail() {
        // A half-decoded chord would light the wrong keys, so this must fail
        // rather than return what it managed to read.
        let mut bytes = NoteState {
            host_clock_us: 1,
            held: vec![(60, 100), (64, 90)],
        }
        .encode();
        bytes.pop();
        assert_eq!(NoteState::decode(&bytes), None);
    }

    #[test]
    fn notes_reject_a_count_that_disagrees_with_the_payload() {
        let mut bytes = NoteState {
            host_clock_us: 1,
            held: vec![(60, 100)],
        }
        .encode();
        bytes[9] = 5; // claims five notes, carries one
        assert_eq!(NoteState::decode(&bytes), None);
    }

    #[test]
    fn notes_truncate_rather_than_overflow_the_count_byte() {
        let state = NoteState {
            host_clock_us: 0,
            held: (0..200u16).map(|i| ((i % 128) as u8, 64)).collect(),
        };
        let decoded = NoteState::decode(&state.encode()).unwrap();
        assert_eq!(decoded.held.len(), 128);
    }

    // ---- Cross-implementation fixtures -------------------------------
    // These exact byte sequences are also asserted by the C# client's check
    // harness. Pinning the same literals on both sides is what makes "the two
    // implementations agree" a fact rather than an intention: if either encoder
    // drifts, one of the two suites fails immediately instead of the headset
    // quietly rendering nonsense.

    #[test]
    fn position_bytes_match_the_client_fixture() {
        let sample = PositionSample {
            song_time_sec: 12.5,
            host_clock_us: 987_654_321,
            playing: true,
        };
        assert_eq!(
            sample.encode().to_vec(),
            vec![
                0x40, //
                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x29, 0x40, // 12.5 f64 LE
                0xB1, 0x68, 0xDE, 0x3A, 0x00, 0x00, 0x00, 0x00, // 987654321 u64 LE
                0x01, // playing
            ]
        );
    }

    #[test]
    fn notes_bytes_match_the_client_fixture() {
        let state = NoteState {
            host_clock_us: 42,
            held: vec![(60, 100), (64, 90), (67, 80)],
        };
        assert_eq!(
            state.encode(),
            vec![
                0x41, //
                0x2A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, // 42 u64 LE
                0x03, // three held
                60, 100, 64, 90, 67, 80,
            ]
        );
    }

    #[test]
    fn empty_notes_bytes_match_the_client_fixture() {
        let state = NoteState {
            host_clock_us: 0,
            held: vec![],
        };
        assert_eq!(state.encode(), vec![0x41, 0, 0, 0, 0, 0, 0, 0, 0, 0x00]);
    }

    #[test]
    fn host_clock_is_monotonic() {
        let a = host_clock_us();
        let b = host_clock_us();
        assert!(b >= a);
    }
}
