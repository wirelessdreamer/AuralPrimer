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
    /// Optional frames. A host that does not implement them says so by
    /// omitting the matching name from `features` in WELCOME; see protocol §6.
    pub const LIBRARY_REQUEST: u8 = 0x12;
    pub const LIBRARY: u8 = 0x13;
    pub const SELECT_SONG: u8 = 0x14;
    pub const VOICE_QUERY: u8 = 0x15;
    pub const VOICE_RESULT: u8 = 0x16;
    pub const KEYBOARD_LAYOUT: u8 = 0x17;
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


// --- Library browsing (protocol §6) ------------------------------------------
//
// The host answers the headset's library questions because it already holds the
// folder and already answers the same question for its own UI. Shipping the whole
// library so the headset could filter locally would mean a second copy of the
// matching rules, free to disagree with the desktop's about what "matches" means.
//
// The matching itself lives here, away from any I/O, so the rules are pinned by
// unit tests rather than by running a headset against a real songs folder.

use serde::{Deserialize, Serialize};

/// One row of the library, as the headset lists it.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibrarySong {
    pub song_id: String,
    pub title: String,
    /// All optional: a song is not required to carry them, and a missing genre
    /// must render as "no genre" rather than as a guess.
    pub artist: Option<String>,
    pub genre: Option<String>,
    pub duration_sec: Option<f64>,
}

/// What the headset asked for.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct LibraryQuery {
    /// Substring of title or artist.
    pub search: Option<String>,
    /// Exact facet filters. `None` means unfiltered.
    pub artist: Option<String>,
    pub genre: Option<String>,
    pub page: u32,
    pub page_size: u32,
}

/// One page of results, plus the facets to filter by.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryPage {
    pub page: u32,
    pub page_size: u32,
    pub total: u32,
    /// Distinct values across the WHOLE library, not just this page — the
    /// headset builds its filter chips from these, and chips that changed every
    /// time you paged would be unusable.
    pub artists: Vec<String>,
    pub genres: Vec<String>,
    pub items: Vec<LibrarySong>,
}

/// Largest page the host will return, whatever was asked for.
///
/// A headset panel shows single figures of rows; a client asking for 100000
/// is either confused or hostile, and either way the host should not build the
/// allocation to find out.
const MAX_PAGE_SIZE: u32 = 64;
const DEFAULT_PAGE_SIZE: u32 = 8;

/// Case-insensitive, accent-naive equality. Facet values come from the library
/// itself so they should already match exactly; folding case costs nothing and
/// removes a whole class of "the chip does nothing" bug.
fn same(a: &str, b: &str) -> bool {
    a.trim().to_lowercase() == b.trim().to_lowercase()
}

fn contains_ci(haystack: &str, needle: &str) -> bool {
    haystack.to_lowercase().contains(&needle.to_lowercase())
}

/// Filter, sort and page the library.
///
/// Sorted by title, case-insensitively, so the order is stable across calls and
/// matches what the desktop library shows by default. Without a defined order,
/// paging is meaningless — page 2 could repeat page 1.
pub fn query_library(all: &[LibrarySong], q: &LibraryQuery) -> LibraryPage {
    let mut artists: Vec<String> = all
        .iter()
        .filter_map(|s| s.artist.clone())
        .filter(|a| !a.trim().is_empty())
        .collect();
    artists.sort_by_key(|a| a.to_lowercase());
    artists.dedup_by(|a, b| same(a, b));

    let mut genres: Vec<String> = all
        .iter()
        .filter_map(|s| s.genre.clone())
        .filter(|g| !g.trim().is_empty())
        .collect();
    genres.sort_by_key(|g| g.to_lowercase());
    genres.dedup_by(|a, b| same(a, b));

    let search = q.search.as_deref().map(str::trim).filter(|s| !s.is_empty());

    let mut matched: Vec<LibrarySong> = all
        .iter()
        .filter(|song| {
            if let Some(needle) = search {
                let hit = contains_ci(&song.title, needle)
                    || song
                        .artist
                        .as_deref()
                        .is_some_and(|a| contains_ci(a, needle));
                if !hit {
                    return false;
                }
            }
            if let Some(want) = q.artist.as_deref().map(str::trim).filter(|a| !a.is_empty()) {
                if !song.artist.as_deref().is_some_and(|a| same(a, want)) {
                    return false;
                }
            }
            if let Some(want) = q.genre.as_deref().map(str::trim).filter(|g| !g.is_empty()) {
                if !song.genre.as_deref().is_some_and(|g| same(g, want)) {
                    return false;
                }
            }
            true
        })
        .cloned()
        .collect();

    matched.sort_by_key(|s| s.title.to_lowercase());

    let total = matched.len() as u32;
    let page_size = match q.page_size {
        0 => DEFAULT_PAGE_SIZE,
        n => n.min(MAX_PAGE_SIZE),
    };
    // Clamp rather than return empty: a headset that asks for page 5 of a list
    // that just shrank to two pages should see the last page, not a blank panel
    // it has no obvious way out of.
    let pages = total.div_ceil(page_size).max(1);
    let page = q.page.min(pages - 1);
    let start = (page * page_size) as usize;
    let items = matched
        .into_iter()
        .skip(start)
        .take(page_size as usize)
        .collect();

    LibraryPage {
        page,
        page_size,
        total,
        artists,
        genres,
        items,
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

#[cfg(test)]
mod library_tests {
    use super::*;

    fn song(id: &str, title: &str, artist: Option<&str>, genre: Option<&str>) -> LibrarySong {
        LibrarySong {
            song_id: id.to_string(),
            title: title.to_string(),
            artist: artist.map(str::to_string),
            genre: genre.map(str::to_string),
            duration_sec: Some(60.0),
        }
    }

    fn library() -> Vec<LibrarySong> {
        vec![
            song("a", "Prelude in C", Some("Bach"), Some("classical")),
            song("b", "Toccata", Some("Bach"), Some("classical")),
            song("c", "Eine kleine Nachtmusik", Some("Mozart"), Some("classical")),
            song("d", "How Long", Some("Psalms"), Some("worship")),
            song("e", "Untagged Take", None, None),
        ]
    }

    fn query(page_size: u32) -> LibraryQuery {
        LibraryQuery {
            page_size,
            ..Default::default()
        }
    }

    #[test]
    fn search_matches_title_or_artist_case_insensitively() {
        let all = library();
        let mut q = query(10);

        q.search = Some("BACH".to_string());
        let by_artist = query_library(&all, &q);
        assert_eq!(by_artist.total, 2);

        q.search = Some("prelude".to_string());
        let by_title = query_library(&all, &q);
        assert_eq!(by_title.total, 1);
        assert_eq!(by_title.items[0].song_id, "a");
    }

    #[test]
    fn blank_search_is_not_a_filter() {
        // A search box the user cleared must show everything, not nothing --
        // an empty needle is contained in every string, but a whitespace-only
        // one would otherwise fall through as a real filter.
        let all = library();
        let mut q = query(10);
        q.search = Some("   ".to_string());
        assert_eq!(query_library(&all, &q).total, all.len() as u32);
    }

    #[test]
    fn facet_filters_are_exact_not_substring() {
        let all = library();
        let mut q = query(10);

        q.artist = Some("Bach".to_string());
        assert_eq!(query_library(&all, &q).total, 2);

        // "Bac" is a substring of "Bach" but not that artist.
        q.artist = Some("Bac".to_string());
        assert_eq!(query_library(&all, &q).total, 0);
    }

    #[test]
    fn untagged_songs_never_collect_under_an_empty_facet() {
        // The song with no genre must not appear under a genre named "", and
        // must not contribute one to the chip list.
        let all = library();
        let page = query_library(&all, &query(10));
        assert!(page.genres.iter().all(|g| !g.trim().is_empty()));
        assert_eq!(page.genres, vec!["classical", "worship"]);

        let mut q = query(10);
        q.genre = Some(String::new());
        assert_eq!(
            query_library(&all, &q).total,
            all.len() as u32,
            "an empty genre filter is no filter"
        );
    }

    #[test]
    fn facets_cover_the_whole_library_not_the_page() {
        // Chips that changed as you paged would be unusable.
        let all = library();
        let mut q = query(1);
        q.page = 0;
        let first = query_library(&all, &q);
        q.page = 3;
        let last = query_library(&all, &q);

        assert_eq!(first.items.len(), 1);
        assert_eq!(first.artists, last.artists);
        assert_eq!(first.genres, last.genres);
        assert_eq!(first.artists, vec!["Bach", "Mozart", "Psalms"]);
    }

    #[test]
    fn results_are_title_sorted_so_paging_means_something() {
        let all = library();
        let page = query_library(&all, &query(10));
        let titles: Vec<&str> = page.items.iter().map(|s| s.title.as_str()).collect();
        let mut sorted = titles.clone();
        sorted.sort_by_key(|t| t.to_lowercase());
        assert_eq!(titles, sorted);
    }

    #[test]
    fn page_beyond_the_end_clamps_to_the_last_one() {
        // A headset holding page 5 of a list that just shrank should land on
        // the last page, not on a blank panel with no obvious way out.
        let all = library();
        let mut q = query(2);
        q.page = 99;
        let page = query_library(&all, &q);
        assert_eq!(page.page, 2, "5 songs at 2 per page is pages 0..=2");
        assert_eq!(page.items.len(), 1);
    }

    #[test]
    fn page_size_is_defaulted_and_capped() {
        let all = library();

        let unspecified = query_library(&all, &query(0));
        assert_eq!(unspecified.page_size, DEFAULT_PAGE_SIZE);

        let greedy = query_library(&all, &query(100_000));
        assert_eq!(greedy.page_size, MAX_PAGE_SIZE);
    }

    #[test]
    fn empty_library_still_answers() {
        let page = query_library(&[], &query(8));
        assert_eq!(page.total, 0);
        assert_eq!(page.page, 0);
        assert!(page.items.is_empty());
        assert!(page.artists.is_empty());
    }

    #[test]
    fn wire_shape_is_camel_case() {
        // The C# client parses these by hand; a silent rename here would break
        // the headset with no compile error anywhere.
        let page = query_library(&library(), &query(1));
        let json = serde_json::to_string(&page).expect("serialisable");
        assert!(json.contains("\"pageSize\""), "{json}");
        assert!(json.contains("\"songId\""), "{json}");
        assert!(json.contains("\"durationSec\""), "{json}");
    }

    #[test]
    fn query_parses_from_the_documented_json() {
        let q: LibraryQuery = serde_json::from_str(
            r#"{"search":"bach","artist":null,"genre":"classical","page":0,"pageSize":8}"#,
        )
        .expect("documented query must parse");
        assert_eq!(q.search.as_deref(), Some("bach"));
        assert_eq!(q.genre.as_deref(), Some("classical"));
        assert_eq!(q.page_size, 8);

        // Absent fields must not be an error: the doc says null or absent means
        // unfiltered, and a client is free to omit them.
        let sparse: LibraryQuery = serde_json::from_str(r#"{"page":1}"#).expect("sparse parses");
        assert_eq!(sparse.page, 1);
        assert!(sparse.search.is_none());
    }
}
