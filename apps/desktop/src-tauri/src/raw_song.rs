use crate::wav_mix::{mix_wavs, read_wav_pcm16, write_wav_pcm16, WavPcm16};
use midly::num::{u15, u24, u28, u4, u7};
use midly::{Format, Header, MetaMessage, MidiMessage, Smf, Timing, TrackEvent, TrackEventKind};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ImportRawSongFolderRequest {
    /// Absolute path to a folder containing stem WAVs and one-or-more MIDI files.
    #[serde(alias = "folderPath")]
    pub folder_path: String,

    /// Optional overrides. If omitted, we derive a title from the folder name.
    pub title: Option<String>,
    pub artist: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn build_tempo_change_test_midi() -> Vec<u8> {
        let smf = Smf {
            header: Header::new(Format::Parallel, Timing::Metrical(u15::new(480))),
            tracks: vec![
                vec![
                    TrackEvent {
                        delta: u28::new(0),
                        kind: TrackEventKind::Meta(MetaMessage::Tempo(u24::new(500_000))),
                    },
                    TrackEvent {
                        delta: u28::new(0),
                        kind: TrackEventKind::Meta(MetaMessage::TimeSignature(4, 2, 24, 8)),
                    },
                    TrackEvent {
                        delta: u28::new(480),
                        kind: TrackEventKind::Meta(MetaMessage::Tempo(u24::new(1_000_000))),
                    },
                    TrackEvent {
                        delta: u28::new(0),
                        kind: TrackEventKind::Meta(MetaMessage::EndOfTrack),
                    },
                ],
                vec![
                    TrackEvent {
                        delta: u28::new(720),
                        kind: TrackEventKind::Midi {
                            channel: u4::new(9),
                            message: MidiMessage::NoteOn {
                                key: u7::new(36),
                                vel: u7::new(100),
                            },
                        },
                    },
                    TrackEvent {
                        delta: u28::new(240),
                        kind: TrackEventKind::Midi {
                            channel: u4::new(9),
                            message: MidiMessage::NoteOff {
                                key: u7::new(36),
                                vel: u7::new(0),
                            },
                        },
                    },
                    TrackEvent {
                        delta: u28::new(0),
                        kind: TrackEventKind::Meta(MetaMessage::EndOfTrack),
                    },
                ],
            ],
        };

        let mut out = vec![];
        smf.write_std(&mut out).expect("encode synthetic midi");
        out
    }

    fn build_single_note_test_midi(note_start_tick: u32, note_len_ticks: u32) -> Vec<u8> {
        let smf = Smf {
            header: Header::new(Format::Parallel, Timing::Metrical(u15::new(480))),
            tracks: vec![
                vec![
                    TrackEvent {
                        delta: u28::new(0),
                        kind: TrackEventKind::Meta(MetaMessage::Tempo(u24::new(500_000))),
                    },
                    TrackEvent {
                        delta: u28::new(0),
                        kind: TrackEventKind::Meta(MetaMessage::TimeSignature(4, 2, 24, 8)),
                    },
                    TrackEvent {
                        delta: u28::new(0),
                        kind: TrackEventKind::Meta(MetaMessage::EndOfTrack),
                    },
                ],
                vec![
                    TrackEvent {
                        delta: u28::new(note_start_tick),
                        kind: TrackEventKind::Midi {
                            channel: u4::new(9),
                            message: MidiMessage::NoteOn {
                                key: u7::new(36),
                                vel: u7::new(100),
                            },
                        },
                    },
                    TrackEvent {
                        delta: u28::new(note_len_ticks.max(1)),
                        kind: TrackEventKind::Midi {
                            channel: u4::new(9),
                            message: MidiMessage::NoteOff {
                                key: u7::new(36),
                                vel: u7::new(0),
                            },
                        },
                    },
                    TrackEvent {
                        delta: u28::new(0),
                        kind: TrackEventKind::Meta(MetaMessage::EndOfTrack),
                    },
                ],
            ],
        };

        let mut out = vec![];
        smf.write_std(&mut out).expect("encode single-note midi");
        out
    }

    fn build_onset_test_wav(
        sample_rate: u32,
        channels: u16,
        total_sec: f64,
        onset_sec: f64,
        active_sec: f64,
        level: i16,
    ) -> WavPcm16 {
        let total_frames = (sample_rate as f64 * total_sec).round() as usize;
        let onset_frame = (sample_rate as f64 * onset_sec).round() as usize;
        let active_frames = (sample_rate as f64 * active_sec).round() as usize;
        let end_frame = onset_frame.saturating_add(active_frames).min(total_frames);
        let mut data = vec![0i16; total_frames * channels as usize];
        for frame in onset_frame.min(total_frames)..end_frame {
            for ch in 0..channels as usize {
                data[frame * channels as usize + ch] = level;
            }
        }
        WavPcm16 {
            sample_rate,
            channels,
            data,
        }
    }

    fn build_source_truth_drum_notes() -> Vec<TimedMidiNote> {
        let mut notes: Vec<TimedMidiNote> = vec![];
        let mut t = 0.20;

        for _ in 0..24 {
            notes.push(TimedMidiNote {
                t_on: t,
                t_off: t + 0.05,
                pitch: 36,
                velocity: 0.92,
            });
            t += 0.18;
        }
        for _ in 0..24 {
            notes.push(TimedMidiNote {
                t_on: t,
                t_off: t + 0.05,
                pitch: 38,
                velocity: 0.88,
            });
            t += 0.18;
        }
        for _ in 0..12 {
            notes.push(TimedMidiNote {
                t_on: t,
                t_off: t + 0.04,
                pitch: 46,
                velocity: 0.72,
            });
            t += 0.18;
        }
        for _ in 0..12 {
            notes.push(TimedMidiNote {
                t_on: t,
                t_off: t + 0.06,
                pitch: 49,
                velocity: 0.76,
            });
            t += 0.18;
        }
        for _ in 0..8 {
            notes.push(TimedMidiNote {
                t_on: t,
                t_off: t + 0.05,
                pitch: 45,
                velocity: 0.90,
            });
            t += 0.18;
        }

        notes
    }

    fn overlay_source_truth_drum_hit(
        samples: &mut [f64],
        sample_rate: u32,
        start_sec: f64,
        pitch: u8,
    ) {
        let sr = sample_rate as f64;
        let start = (start_sec * sr).round().max(0.0) as usize;
        let total = samples.len();

        match pitch {
            36 | 45 => {
                let frames = (0.08 * sr).round() as usize;
                for i in 0..frames {
                    let idx = start + i;
                    if idx >= total {
                        break;
                    }
                    let env = (-6.0 * (i as f64 / frames.max(1) as f64)).exp();
                    let wave = (2.0 * std::f64::consts::PI * 58.0 * (i as f64 / sr)).sin();
                    samples[idx] += 0.85 * wave * env;
                }
            }
            38 => {
                let frames = (0.05 * sr).round() as usize;
                for i in 0..frames {
                    let idx = start + i;
                    if idx >= total {
                        break;
                    }
                    let env = (-12.0 * (i as f64 / frames.max(1) as f64)).exp();
                    let noise = if i % 2 == 0 { 0.55 } else { -0.55 };
                    let crack =
                        (2.0 * std::f64::consts::PI * 2000.0 * (i as f64 / sr)).sin() * 0.25;
                    samples[idx] += (noise * 0.65 + crack) * env;
                }
            }
            46 => {
                let frames = (0.025 * sr).round() as usize;
                for i in 0..frames {
                    let idx = start + i;
                    if idx >= total {
                        break;
                    }
                    let env = (-25.0 * (i as f64 / frames.max(1) as f64)).exp();
                    let noise = if i % 2 == 0 { 0.35 } else { -0.35 };
                    let bright =
                        (2.0 * std::f64::consts::PI * 9000.0 * (i as f64 / sr)).sin() * 0.10;
                    samples[idx] += (noise + bright) * env;
                }
            }
            49 => {
                let frames = (0.12 * sr).round() as usize;
                for i in 0..frames {
                    let idx = start + i;
                    if idx >= total {
                        break;
                    }
                    let env = (-6.0 * (i as f64 / frames.max(1) as f64)).exp();
                    let noise = if i % 2 == 0 { 0.24 } else { -0.24 };
                    let bright =
                        (2.0 * std::f64::consts::PI * 6000.0 * (i as f64 / sr)).sin() * 0.18;
                    samples[idx] += (noise + bright) * env;
                }
            }
            _ => {}
        }
    }

    fn build_source_truth_drum_wav(notes: &[TimedMidiNote], sample_rate: u32) -> WavPcm16 {
        let duration_sec = notes.iter().map(|note| note.t_off).fold(0.0_f64, f64::max) + 0.5;
        let total_frames = (duration_sec * sample_rate as f64).ceil().max(1.0) as usize;
        let mut samples = vec![0.0_f64; total_frames];
        for note in notes {
            overlay_source_truth_drum_hit(&mut samples, sample_rate, note.t_on, note.pitch);
        }

        let peak = samples
            .iter()
            .map(|sample| sample.abs())
            .fold(0.0_f64, f64::max);
        let scale = if peak > 1e-9 { 0.95 / peak } else { 1.0 };
        let data = samples
            .into_iter()
            .map(|sample| {
                let clamped = (sample * scale).clamp(-1.0, 1.0);
                (clamped * 32767.0).round() as i16
            })
            .collect();

        WavPcm16 {
            sample_rate,
            channels: 1,
            data,
        }
    }

    #[test]
    fn midi_bytes_to_timed_notes_respects_cross_track_tempo_map() {
        let midi = build_tempo_change_test_midi();
        let notes = midi_bytes_to_timed_notes(&midi).expect("parse timed notes");
        assert_eq!(notes.len(), 1);
        assert!(
            (notes[0].t_on - 1.0).abs() < 1e-6,
            "unexpected t_on: {}",
            notes[0].t_on
        );
        assert!(
            (notes[0].t_off - 1.5).abs() < 1e-6,
            "unexpected t_off: {}",
            notes[0].t_off
        );
    }

    #[test]
    fn write_combined_gameplay_midi_preserves_tempo_changes() {
        let midi = build_tempo_change_test_midi();
        let smf = Smf::parse(&midi).expect("parse synthetic midi");
        let timing = build_midi_timing_map(&smf).expect("build timing map");
        let notes = midi_bytes_to_timed_notes(&midi).expect("timed notes");
        let tracks = vec![GameplayTrackNotes {
            track_id: "drums".to_string(),
            role: "drums".to_string(),
            name: "Drums".to_string(),
            channel: 9,
            notes,
        }];

        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dst = std::env::temp_dir().join(format!("auralprimer_tempo_{unique}.mid"));
        write_combined_gameplay_midi(&dst, &tracks, &timing).expect("write combined midi");

        let written = fs::read(&dst).expect("read combined midi");
        let reparsed = midi_bytes_to_timed_notes(&written).expect("reparse combined midi");
        fs::remove_file(&dst).ok();

        assert_eq!(reparsed.len(), 1);
        assert!(
            (reparsed[0].t_on - 1.0).abs() < 1e-6,
            "unexpected combined t_on: {}",
            reparsed[0].t_on
        );
        assert!(
            (reparsed[0].t_off - 1.5).abs() < 1e-6,
            "unexpected combined t_off: {}",
            reparsed[0].t_off
        );

        let written_smf = Smf::parse(&written).expect("parse written smf");
        let written_timing = build_midi_timing_map(&written_smf).expect("written timing map");
        assert_eq!(written_timing.tempo_segments.len(), 2);
        assert_eq!(written_timing.tempo_segments[0].tick, 0);
        assert_eq!(written_timing.tempo_segments[0].us_per_quarter, 500_000);
        assert_eq!(written_timing.tempo_segments[1].tick, 480);
        assert_eq!(written_timing.tempo_segments[1].us_per_quarter, 1_000_000);
    }

    #[test]
    fn infer_suno_drum_pitch_mapping_remaps_kick_like_45() {
        let mut profiles = BTreeMap::new();
        profiles.insert(
            36,
            DrumPitchProfile {
                count: 320,
                low_ratio: 0.10,
                high_ratio: 0.42,
                zero_crossing_rate: 0.18,
                rms: 0.14,
            },
        );
        profiles.insert(
            38,
            DrumPitchProfile {
                count: 180,
                low_ratio: 0.05,
                high_ratio: 0.40,
                zero_crossing_rate: 0.26,
                rms: 0.12,
            },
        );
        profiles.insert(
            46,
            DrumPitchProfile {
                count: 90,
                low_ratio: 0.03,
                high_ratio: 0.52,
                zero_crossing_rate: 0.34,
                rms: 0.06,
            },
        );
        profiles.insert(
            57,
            DrumPitchProfile {
                count: 40,
                low_ratio: 0.06,
                high_ratio: 0.45,
                zero_crossing_rate: 0.24,
                rms: 0.10,
            },
        );
        profiles.insert(
            45,
            DrumPitchProfile {
                count: 96,
                low_ratio: 0.16,
                high_ratio: 0.38,
                zero_crossing_rate: 0.17,
                rms: 0.15,
            },
        );

        let mapping = infer_suno_drum_pitch_mapping_from_profiles(&profiles);

        assert_eq!(mapping.get(&45), Some(&36));
    }

    #[test]
    fn import_raw_song_folder_generates_notes_mid_from_source_midis() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("auralprimer_raw_song_{unique}"));
        let source_dir = root.join("source");
        let songs_root = root.join("songs");
        fs::create_dir_all(&source_dir).expect("create source dir");
        fs::create_dir_all(&songs_root).expect("create songs dir");

        let wav = WavPcm16 {
            sample_rate: 48_000,
            channels: 2,
            data: vec![0; 48_000 * 2 * 2],
        };
        write_wav_pcm16(&source_dir.join("Test Song (Drums).wav"), &wav).expect("write wav");
        fs::write(
            source_dir.join("Test Song (Drums).mid"),
            build_tempo_change_test_midi(),
        )
        .expect("write midi");

        let result = import_raw_song_folder(
            ImportRawSongFolderRequest {
                folder_path: source_dir.to_string_lossy().to_string(),
                title: Some("Test Song".to_string()),
                artist: None,
            },
            &songs_root,
        )
        .expect("import raw song folder");

        let out_dir = PathBuf::from(result.songpack_path);
        let copied_midis: Vec<PathBuf> = fs::read_dir(out_dir.join("features").join("midi"))
            .expect("read features/midi")
            .map(|entry| entry.expect("dir entry").path())
            .collect();
        assert_eq!(copied_midis.len(), 1);

        let copied_bytes = fs::read(&copied_midis[0]).expect("read copied midi");
        let written_smf = Smf::parse(&copied_bytes).expect("parse copied midi");
        let written_timing = build_midi_timing_map(&written_smf).expect("written timing map");
        assert_eq!(written_timing.tempo_segments.len(), 2);
        assert_eq!(written_timing.tempo_segments[0].us_per_quarter, 500_000);
        assert_eq!(written_timing.tempo_segments[1].tick, 480);
        assert_eq!(written_timing.tempo_segments[1].us_per_quarter, 1_000_000);

        let notes_mid =
            fs::read(out_dir.join("features").join("notes.mid")).expect("read notes.mid");
        let reparsed = midi_bytes_to_timed_notes(&notes_mid).expect("parse notes.mid");
        assert_eq!(reparsed.len(), 1);
        assert!(
            (reparsed[0].t_on - 1.0).abs() < 1e-6,
            "unexpected normalized t_on: {}",
            reparsed[0].t_on
        );
        assert!(
            (reparsed[0].t_off - 1.5).abs() < 1e-6,
            "unexpected normalized t_off: {}",
            reparsed[0].t_off
        );

        let events_json: serde_json::Value = serde_json::from_slice(
            &fs::read(out_dir.join("features").join("events.json")).expect("read events.json"),
        )
        .expect("parse events.json");
        assert_eq!(
            events_json["notes"]
                .as_array()
                .map(|items| items.len())
                .unwrap_or_default(),
            1
        );

        let manifest: serde_json::Value = serde_json::from_slice(
            &fs::read(out_dir.join("manifest.json")).expect("read manifest"),
        )
        .expect("parse manifest");
        assert_eq!(
            manifest["timing"]["midi_timing_trust"].as_str(),
            Some("normalized_source")
        );
        assert_eq!(
            manifest["timing"]["chart_timing_status"].as_str(),
            Some("normalized_from_source_midi")
        );
        assert_eq!(
            manifest["assets"]["midi"]["timing_authority"].as_str(),
            Some("normalized_source")
        );
        assert_eq!(
            manifest["assets"]["midi"]["notes_path"].as_str(),
            Some("features/notes.mid")
        );
        assert!(
            result.midi_chart_included,
            "expected notes.mid to be reported in the import result"
        );

        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn import_raw_song_folder_normalizes_source_midi_start_offset() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("auralprimer_raw_song_offset_{unique}"));
        let source_dir = root.join("source");
        let songs_root = root.join("songs");
        fs::create_dir_all(&source_dir).expect("create source dir");
        fs::create_dir_all(&songs_root).expect("create songs dir");

        let wav = build_onset_test_wav(48_000, 1, 2.0, 0.40, 0.24, 12_000);
        write_wav_pcm16(&source_dir.join("Offset Test (Drums).wav"), &wav).expect("write wav");
        fs::write(
            source_dir.join("Offset Test (Drums).mid"),
            build_single_note_test_midi(864, 96),
        )
        .expect("write midi");
        let midi_bytes = fs::read(source_dir.join("Offset Test (Drums).mid")).expect("read midi");
        let expected_audio_start = first_active_segment_start_sec(&wav).expect("audio onset");
        let expected_midi_start = first_midi_note_start_sec(&midi_bytes)
            .expect("first midi note parse")
            .expect("first midi note");
        let expected_offset = quantize(expected_midi_start - expected_audio_start, 1e-6);

        let result = import_raw_song_folder(
            ImportRawSongFolderRequest {
                folder_path: source_dir.to_string_lossy().to_string(),
                title: Some("Offset Test".to_string()),
                artist: None,
            },
            &songs_root,
        )
        .expect("import raw song folder");

        let out_dir = PathBuf::from(result.songpack_path);
        let manifest: serde_json::Value = serde_json::from_slice(
            &fs::read(out_dir.join("manifest.json")).expect("read manifest"),
        )
        .expect("parse manifest");
        let source_offset = manifest["timing"]["source_audio_start_offset_sec"]
            .as_f64()
            .expect("source_audio_start_offset_sec");
        assert!(
            (source_offset - expected_offset).abs() < 1e-6,
            "expected source_audio_start_offset_sec to capture the measured offset, got {source_offset}"
        );
        let runtime_offset = manifest["timing"]["audio_start_offset_sec"]
            .as_f64()
            .expect("audio_start_offset_sec");
        assert!(
            runtime_offset.abs() < 1e-9,
            "expected audio_start_offset_sec to remain unset, got {runtime_offset}"
        );

        let events_json: serde_json::Value = serde_json::from_slice(
            &fs::read(out_dir.join("features").join("events.json")).expect("read events.json"),
        )
        .expect("parse events.json");
        assert!(
            events_json["notes"]
                .as_array()
                .map(|items| !items.is_empty())
                .unwrap_or(false),
            "expected normalized Suno source MIDI timings to be written into authoritative events.json"
        );

        let notes_mid =
            fs::read(out_dir.join("features").join("notes.mid")).expect("read notes.mid");
        let notes = midi_bytes_to_timed_notes(&notes_mid).expect("parse notes.mid");
        assert_eq!(notes.len(), 1);
        assert!(
            (notes[0].t_on - expected_audio_start).abs() < 0.02,
            "expected normalized note start near the detected audio onset, got {} vs {}",
            notes[0].t_on,
            expected_audio_start
        );

        assert!(
            result
                .warnings
                .iter()
                .any(|warning| warning.contains("Applied")),
            "expected import warning explaining the normalized Suno offset, got {:?}",
            result.warnings
        );
        assert_eq!(result.source_midi_offset_sec, Some(expected_offset));
        assert_eq!(result.source_midi_offset_pair_count, 1);

        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn inspect_raw_song_folder_warns_when_source_midi_and_audio_are_out_of_sync() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("auralprimer_raw_song_scan_{unique}"));
        fs::create_dir_all(&root).expect("create source dir");

        let wav = build_onset_test_wav(48_000, 1, 2.0, 0.40, 0.24, 12_000);
        write_wav_pcm16(&root.join("Offset Test (Drums).wav"), &wav).expect("write wav");
        fs::write(
            root.join("Offset Test (Drums).mid"),
            build_single_note_test_midi(864, 96),
        )
        .expect("write midi");
        let midi_bytes = fs::read(root.join("Offset Test (Drums).mid")).expect("read midi");
        let expected_audio_start = first_active_segment_start_sec(&wav).expect("audio onset");
        let expected_midi_start = first_midi_note_start_sec(&midi_bytes)
            .expect("first midi note parse")
            .expect("first midi note");
        let expected_offset = quantize(expected_midi_start - expected_audio_start, 1e-6);

        let inspection = inspect_raw_song_folder(&root).expect("inspect raw song folder");
        assert!(
            inspection
                .warnings
                .iter()
                .any(|warning| warning.contains("Studio can normalize this during import")),
            "expected source sync warning, got {:?}",
            inspection.warnings
        );
        assert_eq!(inspection.source_midi_offset_sec, Some(expected_offset));
        assert_eq!(inspection.source_midi_offset_pair_count, 1);

        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn import_raw_song_folder_preserves_suno_drum_source_pitches() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("auralprimer_raw_song_source_truth_{unique}"));
        let source_dir = root.join("source");
        let songs_root = root.join("songs");
        fs::create_dir_all(&source_dir).expect("create source dir");
        fs::create_dir_all(&songs_root).expect("create songs dir");

        let drum_notes = build_source_truth_drum_notes();
        let drum_wav = build_source_truth_drum_wav(&drum_notes, 48_000);
        write_wav_pcm16(&source_dir.join("Source Truth (Drums).wav"), &drum_wav)
            .expect("write drum wav");

        let source_track = GameplayTrackNotes {
            track_id: "drums".to_string(),
            role: "drums".to_string(),
            name: "Drums".to_string(),
            channel: 9,
            notes: drum_notes.clone(),
        };
        write_combined_gameplay_midi(
            &source_dir.join("Source Truth (Drums).mid"),
            &[source_track],
            &default_midi_timing_map(),
        )
        .expect("write source midi");

        let scan = scan_raw_song_folder(&source_dir).expect("scan raw song folder");
        let (source_tracks, _timing) =
            build_combined_gameplay_tracks(&scan).expect("build source tracks");
        let drum_track = source_tracks
            .iter()
            .find(|track| track.role == "drums")
            .expect("drum gameplay track");
        let (_remapped_notes, mapping) =
            remap_suno_drum_notes(&scan, &drum_track.notes).expect("evaluate remap candidates");
        assert_eq!(
            mapping.get(&45),
            Some(&36),
            "expected fixture to produce a kick-like remap suggestion for pitch 45"
        );

        let result = import_raw_song_folder(
            ImportRawSongFolderRequest {
                folder_path: source_dir.to_string_lossy().to_string(),
                title: Some("Source Truth".to_string()),
                artist: None,
            },
            &songs_root,
        )
        .expect("import raw song folder");

        let out_dir = PathBuf::from(result.songpack_path);
        let notes_mid =
            fs::read(out_dir.join("features").join("notes.mid")).expect("read notes.mid");
        let imported_notes = midi_bytes_to_timed_notes(&notes_mid).expect("parse notes.mid");
        let count_pitch = |pitch: u8| {
            imported_notes
                .iter()
                .filter(|note| note.pitch == pitch)
                .count()
        };

        assert_eq!(
            count_pitch(36),
            24,
            "kick count should match the source MIDI exactly"
        );
        assert_eq!(
            count_pitch(45),
            8,
            "tom-floor pitch 45 should be preserved from the source MIDI"
        );
        assert!(
            !result.warnings.iter().any(|warning| warning
                .contains("Canonicalized Suno drum MIDI pitches against the drum stem")),
            "source-of-truth Suno import should not rewrite drum pitches: {:?}",
            result.warnings
        );

        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn import_raw_song_folder_uses_global_offset_when_drum_offset_is_unstable() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("auralprimer_raw_song_global_offset_{unique}"));
        let source_dir = root.join("source");
        let songs_root = root.join("songs");
        fs::create_dir_all(&source_dir).expect("create source dir");
        fs::create_dir_all(&songs_root).expect("create songs dir");

        let drum_wav = build_onset_test_wav(48_000, 1, 5.0, 0.40, 0.20, 12_000);
        let bass_wav = build_onset_test_wav(48_000, 1, 5.0, 0.40, 0.20, 9_000);
        write_wav_pcm16(&source_dir.join("Fallback Test (Drums).wav"), &drum_wav)
            .expect("write drum wav");
        write_wav_pcm16(&source_dir.join("Fallback Test (Bass).wav"), &bass_wav)
            .expect("write bass wav");

        let timing = default_midi_timing_map();
        write_combined_gameplay_midi(
            &source_dir.join("Fallback Test (Drums).mid"),
            &[GameplayTrackNotes {
                track_id: "drums".to_string(),
                role: "drums".to_string(),
                name: "Drums".to_string(),
                channel: 9,
                notes: vec![TimedMidiNote {
                    t_on: 3.40,
                    t_off: 3.55,
                    pitch: 36,
                    velocity: 0.90,
                }],
            }],
            &timing,
        )
        .expect("write drum midi");
        write_combined_gameplay_midi(
            &source_dir.join("Fallback Test (Bass).mid"),
            &[GameplayTrackNotes {
                track_id: "bass".to_string(),
                role: "bass".to_string(),
                name: "Bass".to_string(),
                channel: 0,
                notes: vec![TimedMidiNote {
                    t_on: 1.00,
                    t_off: 1.20,
                    pitch: 40,
                    velocity: 0.82,
                }],
            }],
            &timing,
        )
        .expect("write bass midi");

        let expected_global_offset = quantize(1.00 - 0.40, 1e-6);
        let expected_drum_t_on = quantize(3.40 - expected_global_offset, 1e-6);

        let inspection = inspect_raw_song_folder(&source_dir).expect("inspect raw song folder");
        assert_eq!(
            inspection.source_midi_offset_sec,
            Some(expected_global_offset)
        );
        assert_eq!(inspection.source_midi_offset_pair_count, 1);

        let result = import_raw_song_folder(
            ImportRawSongFolderRequest {
                folder_path: source_dir.to_string_lossy().to_string(),
                title: Some("Fallback Test".to_string()),
                artist: None,
            },
            &songs_root,
        )
        .expect("import raw song folder");

        assert!(
            result.midi_chart_included,
            "expected normalized notes.mid to be written"
        );
        assert!(
            result
                .warnings
                .iter()
                .any(|warning| warning.contains("using the cross-track median correction")),
            "expected unstable drum warning, got {:?}",
            result.warnings
        );
        assert!(
            !result.warnings.iter().any(|warning| warning
                .contains("excluded from the auto-normalized gameplay chart")),
            "drums should fall back to the global offset instead of being excluded: {:?}",
            result.warnings
        );

        let out_dir = PathBuf::from(result.songpack_path);
        let events_json: serde_json::Value = serde_json::from_str(
            &fs::read_to_string(out_dir.join("features").join("events.json"))
                .expect("read events.json"),
        )
        .expect("parse events.json");

        let tracks = events_json
            .get("tracks")
            .and_then(|value| value.as_array())
            .expect("tracks array");
        assert!(
            tracks.iter().any(|track| {
                track.get("track_id").and_then(|value| value.as_str()) == Some("drums")
            }),
            "drum track should remain in the imported gameplay chart"
        );

        let drum_note = events_json
            .get("notes")
            .and_then(|value| value.as_array())
            .expect("notes array")
            .iter()
            .find(|note| note.get("track_id").and_then(|value| value.as_str()) == Some("drums"))
            .expect("drum note in events.json");
        let imported_drum_t_on = drum_note
            .get("t_on")
            .and_then(|value| value.as_f64())
            .expect("drum note start time");
        assert!(
            (imported_drum_t_on - expected_drum_t_on).abs() < 1e-6,
            "expected drum note to use the global offset fallback: got {imported_drum_t_on}, expected {expected_drum_t_on}"
        );

        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn import_raw_song_folder_maps_melodic_roles_from_midi_channels_when_file_role_is_generic() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("auralprimer_raw_song_channel_roles_{unique}"));
        let source_dir = root.join("source");
        let songs_root = root.join("songs");
        fs::create_dir_all(&source_dir).expect("create source dir");
        fs::create_dir_all(&songs_root).expect("create songs dir");

        write_wav_pcm16(
            &source_dir.join("Channel Roles (Bass).wav"),
            &build_onset_test_wav(48_000, 1, 3.0, 0.30, 0.25, 9_000),
        )
        .expect("write bass wav");
        write_wav_pcm16(
            &source_dir.join("Channel Roles (Lead Guitar).wav"),
            &build_onset_test_wav(48_000, 1, 3.0, 0.35, 0.25, 8_000),
        )
        .expect("write guitar wav");
        write_wav_pcm16(
            &source_dir.join("Channel Roles (Keys).wav"),
            &build_onset_test_wav(48_000, 1, 3.0, 0.40, 0.25, 7_000),
        )
        .expect("write keys wav");

        write_combined_gameplay_midi(
            &source_dir.join("Channel Roles.mid"),
            &[
                GameplayTrackNotes {
                    track_id: "part_a".to_string(),
                    role: "bass".to_string(),
                    name: "Part A".to_string(),
                    channel: 0,
                    notes: vec![TimedMidiNote {
                        t_on: 0.90,
                        t_off: 1.15,
                        pitch: 40,
                        velocity: 0.84,
                    }],
                },
                GameplayTrackNotes {
                    track_id: "part_b".to_string(),
                    role: "lead_guitar".to_string(),
                    name: "Part B".to_string(),
                    channel: 2,
                    notes: vec![TimedMidiNote {
                        t_on: 1.10,
                        t_off: 1.28,
                        pitch: 64,
                        velocity: 0.80,
                    }],
                },
                GameplayTrackNotes {
                    track_id: "part_c".to_string(),
                    role: "keys".to_string(),
                    name: "Part C".to_string(),
                    channel: 3,
                    notes: vec![TimedMidiNote {
                        t_on: 1.30,
                        t_off: 1.55,
                        pitch: 72,
                        velocity: 0.78,
                    }],
                },
            ],
            &default_midi_timing_map(),
        )
        .expect("write multi-role midi");

        let inspection = inspect_raw_song_folder(&source_dir).expect("inspect raw song folder");
        assert!(
            inspection.midi_parts.iter().all(|part| part.game_role.is_none()),
            "fixture should rely on per-track channel fallback rather than file-name role detection: {:?}",
            inspection.midi_parts
        );
        assert!(inspection
            .mapped_game_roles
            .iter()
            .any(|role| role == "bass"));
        assert!(inspection
            .mapped_game_roles
            .iter()
            .any(|role| role == "lead_guitar"));
        assert!(inspection
            .mapped_game_roles
            .iter()
            .any(|role| role == "keys"));

        let result = import_raw_song_folder(
            ImportRawSongFolderRequest {
                folder_path: source_dir.to_string_lossy().to_string(),
                title: Some("Channel Roles".to_string()),
                artist: None,
            },
            &songs_root,
        )
        .expect("import raw song folder");

        assert!(
            result.midi_chart_included,
            "expected combined notes.mid to be written"
        );
        assert!(result.mapped_game_roles.iter().any(|role| role == "bass"));
        assert!(result
            .mapped_game_roles
            .iter()
            .any(|role| role == "lead_guitar"));
        assert!(result.mapped_game_roles.iter().any(|role| role == "keys"));

        let out_dir = PathBuf::from(result.songpack_path);
        let events_json: serde_json::Value = serde_json::from_str(
            &fs::read_to_string(out_dir.join("features").join("events.json"))
                .expect("read events.json"),
        )
        .expect("parse events.json");
        let track_roles = events_json
            .get("tracks")
            .and_then(|value| value.as_array())
            .expect("tracks array")
            .iter()
            .filter_map(|track| track.get("role").and_then(|value| value.as_str()))
            .collect::<Vec<_>>();
        assert!(track_roles.contains(&"bass"));
        assert!(track_roles.contains(&"lead_guitar"));
        assert!(track_roles.contains(&"keys"));

        fs::remove_dir_all(&root).ok();
    }
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ImportRawSongFolderResult {
    pub songpack_path: String,
    pub stems_count: usize,
    pub midi_files_count: usize,
    pub lyrics_included: bool,
    pub midi_chart_included: bool,
    pub mapped_game_roles: Vec<String>,
    pub source_midi_offset_sec: Option<f64>,
    pub source_midi_offset_pair_count: usize,
    pub warnings: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct RawSongDetectedPart {
    pub path: String,
    pub detected_role: String,
    pub game_role: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct RawSongFolderInspection {
    pub folder_path: String,
    pub title_guess: String,
    pub stem_wav_paths: Vec<String>,
    pub midi_paths: Vec<String>,
    pub stem_parts: Vec<RawSongDetectedPart>,
    pub midi_parts: Vec<RawSongDetectedPart>,
    pub lyrics_txt_path: Option<String>,
    pub karaoke_json_path: Option<String>,
    pub vocal_stem_path: Option<String>,
    pub mix_wav_path: Option<String>,
    pub mapped_game_roles: Vec<String>,
    pub midi_chart_ready: bool,
    pub source_midi_offset_sec: Option<f64>,
    pub source_midi_offset_pair_count: usize,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone)]
struct DetectedSongPartScan {
    path: PathBuf,
    detected_role: String,
    game_role: Option<String>,
}

#[derive(Debug, Clone)]
struct RawSongFolderScan {
    folder_path: PathBuf,
    title_guess: String,
    stem_wavs: Vec<PathBuf>,
    midi_files: Vec<PathBuf>,
    stem_parts: Vec<DetectedSongPartScan>,
    midi_parts: Vec<DetectedSongPartScan>,
    lyrics_txt: Option<PathBuf>,
    karaoke_json: Option<PathBuf>,
    vocal_stem: Option<PathBuf>,
    mix_wav: Option<PathBuf>,
    mapped_game_roles: Vec<String>,
    midi_chart_ready: bool,
    source_midi_offset_sec: Option<f64>,
    source_midi_offset_pair_count: usize,
    warnings: Vec<String>,
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    let digest = h.finalize();
    hex::encode(digest)
}

fn sanitize_id(s: &str) -> String {
    let mut out = String::new();
    for ch in s.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
        } else if ch == ' ' || ch == '-' || ch == '_' {
            out.push('_');
        }
    }
    while out.contains("__") {
        out = out.replace("__", "_");
    }
    out.trim_matches('_').to_string()
}

fn path_display_name(path: &Path) -> String {
    path.file_name()
        .and_then(|s| s.to_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| path.to_string_lossy().into_owned())
}

fn tokenize_file_stem(path: &Path) -> Vec<String> {
    path.file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .split(|ch: char| !ch.is_ascii_alphanumeric())
        .filter(|part| !part.is_empty())
        .map(|part| part.to_ascii_lowercase())
        .collect()
}

fn token_matches(tokens: &[String], expected: &str) -> bool {
    tokens.iter().any(|token| token == expected)
}

fn token_matches_any(tokens: &[String], expected: &[&str]) -> bool {
    expected.iter().any(|needle| token_matches(tokens, needle))
}

fn looks_like_vocal_wav(path: &Path) -> bool {
    let tokens = tokenize_file_stem(path);
    tokens.iter().any(|t| {
        t == "vocal"
            || t == "vocals"
            || t == "vox"
            || t == "leadvox"
            || t == "leadvocal"
            || t == "lyrics"
    })
}

fn detect_part_role_from_tokens(tokens: &[String], allow_mix: bool) -> String {
    if allow_mix && looks_like_mix_tokens(tokens) {
        return "mix".to_string();
    }
    if token_matches_any(tokens, &["backing", "background", "bgv"])
        && token_matches_any(
            tokens,
            &["vocal", "vocals", "vox", "harmony", "harmonies", "choir"],
        )
    {
        return "backing_vocals".to_string();
    }
    if token_matches_any(
        tokens,
        &["vocal", "vocals", "vox", "leadvox", "leadvocal", "lyrics"],
    ) {
        return "vocals".to_string();
    }
    if token_matches_any(tokens, &["drum", "drums", "kit", "percussion", "perc"]) {
        return "drums".to_string();
    }
    if token_matches_any(tokens, &["bass", "subbass"]) {
        return "bass".to_string();
    }
    if token_matches(tokens, "rhythm") && token_matches_any(tokens, &["guitar", "gtr"]) {
        return "rhythm_guitar".to_string();
    }
    if token_matches(tokens, "lead") && token_matches_any(tokens, &["guitar", "gtr"]) {
        return "lead_guitar".to_string();
    }
    if token_matches_any(tokens, &["guitar", "gtr"]) {
        return "guitar".to_string();
    }
    if token_matches_any(tokens, &["synth", "synths", "pad", "pads", "arp", "arps"]) {
        return "synth".to_string();
    }
    if token_matches_any(
        tokens,
        &["keyboard", "keyboards", "keys", "key", "piano", "organ"],
    ) {
        return "keys".to_string();
    }
    if token_matches_any(tokens, &["fx", "sfx", "effect", "effects"]) {
        return "fx".to_string();
    }
    "unknown".to_string()
}

fn looks_like_mix_tokens(tokens: &[String]) -> bool {
    token_matches_any(tokens, &["mix", "master", "instrumental"])
        || (token_matches(tokens, "full")
            && !token_matches_any(tokens, &["vocal", "vocals", "vox"]))
}

fn map_detected_role_to_game_role(role: &str) -> Option<&'static str> {
    match role {
        "drums" => Some("drums"),
        "bass" => Some("bass"),
        "rhythm_guitar" => Some("rhythm_guitar"),
        "lead_guitar" => Some("lead_guitar"),
        "guitar" => Some("lead_guitar"),
        "keys" | "synth" => Some("keys"),
        "vocals" | "backing_vocals" => Some("vocals"),
        _ => None,
    }
}

fn gameplay_role_for_midi_channel(channel: u8) -> Option<&'static str> {
    match channel {
        9 => Some("drums"),
        0 => Some("bass"),
        1 => Some("rhythm_guitar"),
        2 => Some("lead_guitar"),
        3 => Some("keys"),
        5 => Some("vocals"),
        _ => None,
    }
}

fn inspect_midi_bytes_for_role(midi_bytes: &[u8]) -> Option<String> {
    let smf = Smf::parse(midi_bytes).ok()?;
    let mut tokens: Vec<String> = vec![];
    let mut channels: BTreeSet<u8> = BTreeSet::new();
    let mut note_count = 0usize;

    for track in &smf.tracks {
        for ev in track {
            match &ev.kind {
                TrackEventKind::Meta(MetaMessage::TrackName(raw)) => {
                    let name = String::from_utf8_lossy(raw);
                    let mut name_tokens = name
                        .split(|ch: char| !ch.is_ascii_alphanumeric())
                        .filter(|part| !part.is_empty())
                        .map(|part| part.to_ascii_lowercase())
                        .collect::<Vec<_>>();
                    tokens.append(&mut name_tokens);
                }
                TrackEventKind::Midi { channel, message } => {
                    let ch = channel.as_int() as u8;
                    match message {
                        MidiMessage::NoteOn { vel, .. } if vel.as_int() > 0 => {
                            channels.insert(ch);
                            note_count += 1;
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        }
    }

    if note_count == 0 {
        return None;
    }
    if channels.contains(&9) {
        return Some("drums".to_string());
    }

    let channel_roles: BTreeSet<&'static str> = channels
        .iter()
        .filter_map(|channel| gameplay_role_for_midi_channel(*channel))
        .collect();
    if channel_roles.len() == 1 {
        return channel_roles.iter().next().map(|role| (*role).to_string());
    }

    let detected = detect_part_role_from_tokens(&tokens, false);
    if detected != "unknown" {
        Some(detected)
    } else {
        None
    }
}

fn detect_stem_part(path: &Path) -> DetectedSongPartScan {
    let tokens = tokenize_file_stem(path);
    let detected_role = detect_part_role_from_tokens(&tokens, true);
    let game_role = map_detected_role_to_game_role(&detected_role).map(|role| role.to_string());
    DetectedSongPartScan {
        path: path.to_path_buf(),
        detected_role,
        game_role,
    }
}

fn detect_midi_part(path: &Path) -> DetectedSongPartScan {
    let tokens = tokenize_file_stem(path);
    let mut detected_role = detect_part_role_from_tokens(&tokens, false);
    if detected_role == "unknown" {
        if let Ok(bytes) = fs::read(path) {
            if let Some(inferred) = inspect_midi_bytes_for_role(&bytes) {
                detected_role = inferred;
            }
        }
    }
    let game_role = map_detected_role_to_game_role(&detected_role).map(|role| role.to_string());
    DetectedSongPartScan {
        path: path.to_path_buf(),
        detected_role,
        game_role,
    }
}

fn export_detected_part(part: &DetectedSongPartScan) -> RawSongDetectedPart {
    RawSongDetectedPart {
        path: part.path.to_string_lossy().to_string(),
        detected_role: part.detected_role.clone(),
        game_role: part.game_role.clone(),
    }
}

fn gameplay_role_label(role: &str) -> &'static str {
    match role {
        "drums" => "Drums",
        "bass" => "Bass",
        "lead_guitar" => "Lead Guitar",
        "rhythm_guitar" => "Rhythm Guitar",
        "keys" => "Keys / Synth",
        "vocals" => "Vocals",
        _ => "Other",
    }
}

fn canonical_track_name_for_role(role: &str) -> &'static str {
    match role {
        "drums" => "Drums",
        "bass" => "Bass",
        "lead_guitar" => "Lead Guitar",
        "rhythm_guitar" => "Rhythm Guitar",
        "keys" => "Keys",
        "vocals" => "Vocals",
        _ => "MIDI",
    }
}

fn canonical_channel_for_role(role: &str) -> u8 {
    match role {
        "drums" => 9,
        "bass" => 0,
        "rhythm_guitar" => 1,
        "lead_guitar" => 2,
        "keys" => 3,
        "vocals" => 5,
        _ => 4,
    }
}

fn gameplay_role_sort_key(role: &str) -> usize {
    match role {
        "drums" => 0,
        "bass" => 1,
        "rhythm_guitar" => 2,
        "lead_guitar" => 3,
        "keys" => 4,
        "vocals" => 5,
        _ => 100,
    }
}

fn unique_gameplay_roles_from_parts(scan: &RawSongFolderScan) -> Vec<String> {
    let mut roles: BTreeSet<String> = scan
        .midi_parts
        .iter()
        .filter_map(|part| part.game_role.clone())
        .collect();
    if scan.lyrics_txt.is_some() || scan.karaoke_json.is_some() || scan.vocal_stem.is_some() {
        roles.insert("vocals".to_string());
    }
    roles.into_iter().collect()
}

fn wav_duration_sec_from_pcm(w: &WavPcm16) -> f64 {
    let frames = (w.data.len() as f64) / (w.channels as f64);
    frames / (w.sample_rate as f64)
}

fn quantize(t: f64, q: f64) -> f64 {
    (t / q).round() * q
}

fn shift_time_sec(t: f64, offset_sec: f64) -> f64 {
    quantize((t - offset_sec).max(0.0), 1e-6)
}

fn normalize_lyrics_lines(raw: &str) -> Vec<String> {
    raw.replace("\r\n", "\n")
        .split('\n')
        .map(|line| line.trim())
        .filter(|line| !line.is_empty())
        .map(|line| line.to_string())
        .collect()
}

fn count_visible_chars(text: &str) -> usize {
    text.chars().filter(|ch| !ch.is_whitespace()).count().max(1)
}

fn split_word_chunks(text: &str) -> Vec<(usize, usize, String)> {
    let chars: Vec<char> = text.chars().collect();
    let mut starts: Vec<usize> = vec![];
    let mut in_word = false;
    for (idx, ch) in chars.iter().enumerate() {
        if ch.is_whitespace() {
            in_word = false;
            continue;
        }
        if !in_word {
            starts.push(idx);
            in_word = true;
        }
    }

    if starts.is_empty() {
        return vec![(0, chars.len(), text.to_string())];
    }

    let mut chunks: Vec<(usize, usize, String)> = vec![];
    for (idx, start) in starts.iter().enumerate() {
        let end = starts.get(idx + 1).copied().unwrap_or(chars.len());
        let chunk_text: String = chars[*start..end].iter().collect();
        chunks.push((*start, end, chunk_text));
    }
    chunks
}

fn build_word_timed_chunks(text: &str, start: f64, end: f64) -> Vec<serde_json::Value> {
    let chunks = split_word_chunks(text);
    let duration = (end - start).max(0.05);
    let total_weight: f64 = chunks
        .iter()
        .map(|(_, _, chunk_text)| count_visible_chars(chunk_text) as f64)
        .sum::<f64>()
        .max(1.0);

    let mut acc = 0.0;
    chunks
        .iter()
        .enumerate()
        .map(|(idx, (char_start, char_end, chunk_text))| {
            let weight = count_visible_chars(chunk_text) as f64;
            let chunk_start = start + duration * (acc / total_weight);
            acc += weight;
            let chunk_end = if idx + 1 == chunks.len() {
                end
            } else {
                start + duration * (acc / total_weight)
            };

            serde_json::json!({
                "start": quantize(chunk_start, 1e-6),
                "end": quantize(chunk_end.max(chunk_start + 0.01).min(end.max(chunk_start + 0.01)), 1e-6),
                "text": chunk_text,
                "char_start": char_start,
                "char_end": char_end,
            })
        })
        .collect()
}

fn build_lyrics_json_from_ranges(
    lines: &[String],
    ranges: &[(f64, f64)],
    job_id: &str,
) -> serde_json::Value {
    let lyric_lines: Vec<serde_json::Value> = lines
        .iter()
        .zip(ranges.iter())
        .map(|(text, (start, end))| {
            let line_start = quantize(*start, 1e-6);
            let line_end = quantize(end.max(start + 0.05), 1e-6);
            serde_json::json!({
                "start": line_start,
                "end": line_end,
                "text": text,
                "chunks": build_word_timed_chunks(text, line_start, line_end),
            })
        })
        .collect();

    serde_json::json!({
        "format": "psalms_karaoke_json_v1",
        "granularity": "word",
        "job_id": job_id,
        "lines": lyric_lines,
    })
}

fn build_uniform_lyrics_json(
    raw_text: &str,
    duration_sec: f64,
    job_id: &str,
) -> Result<serde_json::Value, String> {
    let lines = normalize_lyrics_lines(raw_text);
    if lines.is_empty() {
        return Err("lyrics text contained no non-empty lines".to_string());
    }

    let total_weight: f64 = lines
        .iter()
        .map(|line| count_visible_chars(line) as f64)
        .sum::<f64>()
        .max(1.0);

    let mut acc = 0.0;
    let mut ranges: Vec<(f64, f64)> = vec![];
    for (idx, line) in lines.iter().enumerate() {
        let start = duration_sec * (acc / total_weight);
        acc += count_visible_chars(line) as f64;
        let end = if idx + 1 == lines.len() {
            duration_sec
        } else {
            duration_sec * (acc / total_weight)
        };
        ranges.push((start, end.max(start + 0.05)));
    }

    Ok(build_lyrics_json_from_ranges(&lines, &ranges, job_id))
}

fn percentile(sorted: &[f64], fraction: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let frac = fraction.clamp(0.0, 1.0);
    let idx = ((sorted.len() - 1) as f64 * frac).round() as usize;
    sorted[idx]
}

fn smoothed(values: &[f64], radius: usize) -> Vec<f64> {
    if values.is_empty() {
        return vec![];
    }
    let mut out = vec![0.0; values.len()];
    for (idx, slot) in out.iter_mut().enumerate() {
        let start = idx.saturating_sub(radius);
        let end = (idx + radius + 1).min(values.len());
        let sum: f64 = values[start..end].iter().copied().sum();
        *slot = sum / ((end - start) as f64);
    }
    out
}

fn detect_active_segments(wav: &WavPcm16) -> Vec<(f64, f64)> {
    let channels = wav.channels as usize;
    if channels == 0 || wav.sample_rate == 0 || wav.data.is_empty() {
        return vec![];
    }

    let total_frames = wav.data.len() / channels;
    if total_frames == 0 {
        return vec![];
    }

    let frame_size = ((wav.sample_rate as f64) * 0.04).round() as usize;
    let hop_size = ((wav.sample_rate as f64) * 0.02).round() as usize;
    let frame_size = frame_size.max(256);
    let hop_size = hop_size.max(128);

    let mut envelope: Vec<(f64, f64)> = vec![];
    let mut frame_start = 0usize;
    while frame_start < total_frames {
        let frame_end = (frame_start + frame_size).min(total_frames);
        let mut sum_sq = 0.0;
        let mut count = 0usize;
        for frame_idx in frame_start..frame_end {
            let mut mono = 0.0;
            for ch in 0..channels {
                let sample = wav.data[frame_idx * channels + ch] as f64 / 32768.0;
                mono += sample;
            }
            mono /= channels as f64;
            sum_sq += mono * mono;
            count += 1;
        }
        let rms = if count > 0 {
            (sum_sq / count as f64).sqrt()
        } else {
            0.0
        };
        envelope.push((frame_start as f64 / wav.sample_rate as f64, rms));
        if frame_end == total_frames {
            break;
        }
        frame_start += hop_size;
    }

    if envelope.is_empty() {
        return vec![];
    }

    let smoothed_values = smoothed(
        &envelope.iter().map(|(_, value)| *value).collect::<Vec<_>>(),
        2,
    );
    let max_value = smoothed_values.iter().copied().fold(0.0, f64::max);
    if max_value <= 1e-5 {
        return vec![];
    }

    let mut sorted = smoothed_values.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let threshold = (percentile(&sorted, 0.20) * 2.25)
        .max(max_value * 0.14)
        .max(0.0015);
    let frame_span = hop_size as f64 / wav.sample_rate as f64;
    let min_segment_duration = 0.16;
    let max_bridge_gap = 0.18;

    let mut segments: Vec<(f64, f64)> = vec![];
    let mut current_start: Option<f64> = None;
    let mut last_time = 0.0;
    for ((time, _), value) in envelope.iter().zip(smoothed_values.iter()) {
        let active = *value >= threshold;
        if active {
            if current_start.is_none() {
                current_start = Some(*time);
            }
            last_time = *time + frame_span;
        } else if let Some(start) = current_start.take() {
            let end = last_time.max(start + frame_span);
            if end - start >= min_segment_duration {
                segments.push((start, end));
            }
        }
    }
    if let Some(start) = current_start {
        let end = last_time.max(start + frame_span);
        if end - start >= min_segment_duration {
            segments.push((start, end));
        }
    }

    let mut merged: Vec<(f64, f64)> = vec![];
    for (start, end) in segments {
        if let Some((_, prev_end)) = merged.last_mut() {
            if start - *prev_end <= max_bridge_gap {
                *prev_end = end;
                continue;
            }
        }
        merged.push((start, end));
    }
    merged
}

fn first_active_segment_start_sec(wav: &WavPcm16) -> Option<f64> {
    detect_active_segments(wav)
        .into_iter()
        .map(|(start, _)| start)
        .find(|start| start.is_finite())
}

fn active_position_to_time(
    segments: &[(f64, f64)],
    mut active_pos: f64,
    prefer_next_segment: bool,
) -> f64 {
    const EPS: f64 = 1e-9;
    for (idx, (start, end)) in segments.iter().copied().enumerate() {
        let dur = (end - start).max(0.0);
        if active_pos < dur - EPS {
            return start + active_pos;
        }
        if (active_pos - dur).abs() <= EPS {
            if prefer_next_segment && idx + 1 < segments.len() {
                return segments[idx + 1].0;
            }
            return end;
        }
        active_pos -= dur;
    }
    segments.last().map(|(_, end)| *end).unwrap_or(0.0)
}

fn build_aligned_lyrics_json(
    raw_text: &str,
    wav: &WavPcm16,
    job_id: &str,
) -> Result<serde_json::Value, String> {
    let lines = normalize_lyrics_lines(raw_text);
    if lines.is_empty() {
        return Err("lyrics text contained no non-empty lines".to_string());
    }

    let duration_sec = wav_duration_sec_from_pcm(wav).max(0.05);
    let segments = detect_active_segments(wav);
    if segments.is_empty() {
        return build_uniform_lyrics_json(raw_text, duration_sec, job_id);
    }

    let active_total = segments
        .iter()
        .map(|(start, end)| (end - start).max(0.0))
        .sum::<f64>();
    if active_total <= 1e-6 {
        return build_uniform_lyrics_json(raw_text, duration_sec, job_id);
    }

    let total_weight: f64 = lines
        .iter()
        .map(|line| count_visible_chars(line) as f64)
        .sum::<f64>()
        .max(1.0);

    let mut consumed_weight = 0.0;
    let mut ranges: Vec<(f64, f64)> = vec![];
    for (idx, line) in lines.iter().enumerate() {
        let line_weight = count_visible_chars(line) as f64;
        let start_active = active_total * (consumed_weight / total_weight);
        consumed_weight += line_weight;
        let end_active = if idx + 1 == lines.len() {
            active_total
        } else {
            active_total * (consumed_weight / total_weight)
        };

        let start = active_position_to_time(&segments, start_active, idx > 0);
        let end = active_position_to_time(&segments, end_active, false).max(start + 0.05);
        ranges.push((start.min(duration_sec), end.min(duration_sec)));
    }

    Ok(build_lyrics_json_from_ranges(&lines, &ranges, job_id))
}

fn first_midi_note_start_sec(midi_bytes: &[u8]) -> Result<Option<f64>, String> {
    let notes = midi_bytes_to_timed_notes(midi_bytes)?;
    Ok(notes
        .iter()
        .map(|note| note.t_on)
        .filter(|t| t.is_finite())
        .min_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal)))
}

#[derive(Debug, Clone, Copy)]
struct MidiTempoEvent {
    tick: u32,
    us_per_quarter: u32,
}

#[derive(Debug, Clone, Copy)]
struct MidiTimeSignatureEvent {
    tick: u32,
    numerator: u8,
    denominator_pow: u8,
    clocks_per_click: u8,
    notated_32nds_per_beat: u8,
}

#[derive(Debug, Clone, Copy)]
struct MidiTempoSegment {
    tick: u32,
    sec: f64,
    us_per_quarter: u32,
}

#[derive(Debug, Clone)]
struct MidiTimingMap {
    tpq: u16,
    tempo_segments: Vec<MidiTempoSegment>,
    time_signatures: Vec<MidiTimeSignatureEvent>,
}

fn default_midi_timing_map() -> MidiTimingMap {
    MidiTimingMap {
        tpq: 480,
        tempo_segments: vec![MidiTempoSegment {
            tick: 0,
            sec: 0.0,
            us_per_quarter: 500_000,
        }],
        time_signatures: vec![MidiTimeSignatureEvent {
            tick: 0,
            numerator: 4,
            denominator_pow: 2,
            clocks_per_click: 24,
            notated_32nds_per_beat: 8,
        }],
    }
}

fn tick_delta_to_sec(delta_ticks: u32, tpq: u16, us_per_quarter: u32) -> f64 {
    if tpq == 0 {
        return 0.0;
    }
    (delta_ticks as f64) * (us_per_quarter as f64) / ((tpq as f64) * 1_000_000.0)
}

fn normalize_tempo_events(mut events: Vec<MidiTempoEvent>) -> Vec<MidiTempoEvent> {
    if !events.iter().any(|event| event.tick == 0) {
        events.push(MidiTempoEvent {
            tick: 0,
            us_per_quarter: 500_000,
        });
    }
    events.sort_by_key(|event| event.tick);

    let mut out: Vec<MidiTempoEvent> = vec![];
    for event in events {
        if let Some(last) = out.last_mut() {
            if last.tick == event.tick {
                *last = event;
                continue;
            }
        }
        out.push(event);
    }
    out
}

fn normalize_time_signature_events(
    mut events: Vec<MidiTimeSignatureEvent>,
) -> Vec<MidiTimeSignatureEvent> {
    if !events.iter().any(|event| event.tick == 0) {
        events.push(MidiTimeSignatureEvent {
            tick: 0,
            numerator: 4,
            denominator_pow: 2,
            clocks_per_click: 24,
            notated_32nds_per_beat: 8,
        });
    }
    events.sort_by_key(|event| event.tick);

    let mut out: Vec<MidiTimeSignatureEvent> = vec![];
    for event in events {
        if let Some(last) = out.last_mut() {
            if last.tick == event.tick {
                *last = event;
                continue;
            }
        }
        out.push(event);
    }
    out
}

fn build_midi_timing_map(smf: &Smf<'_>) -> Result<MidiTimingMap, String> {
    let tpq = match smf.header.timing {
        Timing::Metrical(t) => t.as_int(),
        Timing::Timecode(_, _) => {
            return Err("unsupported MIDI timing (SMPTE timecode)".to_string())
        }
    };

    let mut tempo_events: Vec<MidiTempoEvent> = vec![];
    let mut time_signatures: Vec<MidiTimeSignatureEvent> = vec![];

    for track in &smf.tracks {
        let mut t_ticks: u32 = 0;
        for ev in track {
            t_ticks = t_ticks.saturating_add(ev.delta.as_int() as u32);
            if let TrackEventKind::Meta(meta) = &ev.kind {
                match meta {
                    MetaMessage::Tempo(us) => tempo_events.push(MidiTempoEvent {
                        tick: t_ticks,
                        us_per_quarter: (*us).as_int(),
                    }),
                    MetaMessage::TimeSignature(
                        numerator,
                        denominator_pow,
                        clocks_per_click,
                        notated_32nds_per_beat,
                    ) => {
                        time_signatures.push(MidiTimeSignatureEvent {
                            tick: t_ticks,
                            numerator: *numerator,
                            denominator_pow: *denominator_pow,
                            clocks_per_click: *clocks_per_click,
                            notated_32nds_per_beat: *notated_32nds_per_beat,
                        });
                    }
                    _ => {}
                }
            }
        }
    }

    let tempo_events = normalize_tempo_events(tempo_events);
    let time_signatures = normalize_time_signature_events(time_signatures);

    let mut tempo_segments: Vec<MidiTempoSegment> = vec![];
    let mut current_tick = tempo_events[0].tick;
    let mut current_sec = 0.0;
    let mut current_tempo = tempo_events[0].us_per_quarter;
    tempo_segments.push(MidiTempoSegment {
        tick: current_tick,
        sec: current_sec,
        us_per_quarter: current_tempo,
    });

    for event in tempo_events.iter().skip(1) {
        if event.tick > current_tick {
            current_sec += tick_delta_to_sec(event.tick - current_tick, tpq, current_tempo);
            current_tick = event.tick;
            current_tempo = event.us_per_quarter;
            tempo_segments.push(MidiTempoSegment {
                tick: current_tick,
                sec: current_sec,
                us_per_quarter: current_tempo,
            });
        } else {
            current_tempo = event.us_per_quarter;
            if let Some(last) = tempo_segments.last_mut() {
                last.us_per_quarter = current_tempo;
            }
        }
    }

    Ok(MidiTimingMap {
        tpq,
        tempo_segments,
        time_signatures,
    })
}

fn tick_to_sec_with_timing(t_ticks: u32, timing: &MidiTimingMap) -> f64 {
    let segments = &timing.tempo_segments;
    if segments.is_empty() {
        return 0.0;
    }

    let mut lo = 0usize;
    let mut hi = segments.len() - 1;
    while lo < hi {
        let mid = (lo + hi + 1) / 2;
        if segments[mid].tick <= t_ticks {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }

    let seg = segments[lo];
    seg.sec
        + tick_delta_to_sec(
            t_ticks.saturating_sub(seg.tick),
            timing.tpq,
            seg.us_per_quarter,
        )
}

fn sec_to_tick_with_timing(sec: f64, timing: &MidiTimingMap) -> u32 {
    let target_sec = sec.max(0.0);
    let segments = &timing.tempo_segments;
    if segments.is_empty() {
        return 0;
    }

    let mut lo = 0usize;
    let mut hi = segments.len() - 1;
    while lo < hi {
        let mid = (lo + hi + 1) / 2;
        if segments[mid].sec <= target_sec {
            lo = mid;
        } else {
            hi = mid - 1;
        }
    }

    let seg = segments[lo];
    let sec_per_beat = (seg.us_per_quarter as f64) / 1_000_000.0;
    let beats = if sec_per_beat <= 0.0 {
        0.0
    } else {
        (target_sec - seg.sec) / sec_per_beat
    };
    let ticks = (seg.tick as f64) + beats * (timing.tpq as f64);
    ticks.max(0.0).round() as u32
}

fn active_time_signature_for_tick(tick: u32, timing: &MidiTimingMap) -> MidiTimeSignatureEvent {
    let mut current = timing
        .time_signatures
        .first()
        .copied()
        .unwrap_or(MidiTimeSignatureEvent {
            tick: 0,
            numerator: 4,
            denominator_pow: 2,
            clocks_per_click: 24,
            notated_32nds_per_beat: 8,
        });
    for event in &timing.time_signatures {
        if event.tick > tick {
            break;
        }
        current = *event;
    }
    current
}

fn time_signature_string(event: MidiTimeSignatureEvent) -> String {
    let denominator = 1u32
        .checked_shl(event.denominator_pow as u32)
        .unwrap_or(4)
        .max(1);
    format!("{}/{}", event.numerator.max(1), denominator)
}

fn generate_beats_from_timing(duration_sec: f64, timing: &MidiTimingMap) -> serde_json::Value {
    let mut beats: Vec<serde_json::Value> = vec![];
    let mut bar = 0i32;
    let mut beat_in_bar = 0i32;
    let mut tick = 0u32;

    while tick_to_sec_with_timing(tick, timing) <= duration_sec + 1e-9 {
        let time_sig = active_time_signature_for_tick(tick, timing);
        let denominator = 1u32
            .checked_shl(time_sig.denominator_pow as u32)
            .unwrap_or(4)
            .max(1);
        let ticks_per_beat = ((timing.tpq as u32) * 4 / denominator).max(1);
        let beats_per_bar = i32::from(time_sig.numerator.max(1));
        let strength = if beat_in_bar == 0 { 1.0 } else { 0.5 };

        beats.push(serde_json::json!({
            "t": quantize(tick_to_sec_with_timing(tick, timing), 1e-6),
            "bar": bar,
            "beat": beat_in_bar,
            "strength": strength,
        }));

        beat_in_bar += 1;
        if beat_in_bar >= beats_per_bar {
            beat_in_bar = 0;
            bar += 1;
        }
        tick = tick.saturating_add(ticks_per_beat);
    }

    serde_json::json!({"beats_version": "1.0.0", "beats": beats})
}

fn generate_tempo_map_from_timing(timing: &MidiTimingMap) -> serde_json::Value {
    let segments: Vec<serde_json::Value> = timing
        .tempo_segments
        .iter()
        .map(|segment| {
            let bpm = 60_000_000.0 / (segment.us_per_quarter as f64);
            let time_sig = active_time_signature_for_tick(segment.tick, timing);
            serde_json::json!({
                "t0": quantize(segment.sec, 1e-6),
                "bpm": quantize(bpm, 1e-3),
                "time_signature": time_signature_string(time_sig),
            })
        })
        .collect();

    serde_json::json!({
        "tempo_version": "1.0.0",
        "segments": segments
    })
}

fn generate_sections_from_beats(
    duration_sec: f64,
    beats: &serde_json::Value,
    bars_per_section: i32,
) -> serde_json::Value {
    let mut starts: Vec<f64> = vec![0.0];
    if let Some(items) = beats.get("beats").and_then(|value| value.as_array()) {
        for beat in items {
            let bar = beat
                .get("bar")
                .and_then(|value| value.as_i64())
                .unwrap_or(0) as i32;
            let beat_in_bar = beat
                .get("beat")
                .and_then(|value| value.as_i64())
                .unwrap_or(0) as i32;
            let t = beat
                .get("t")
                .and_then(|value| value.as_f64())
                .unwrap_or(0.0);
            if bar > 0 && beat_in_bar == 0 && bar % bars_per_section.max(1) == 0 {
                starts.push(t);
            }
        }
    }

    starts.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    starts.dedup_by(|a, b| (*a - *b).abs() < 1e-9);

    let mut sections: Vec<serde_json::Value> = vec![];
    for (idx, t0) in starts.iter().enumerate() {
        let next = starts.get(idx + 1).copied().unwrap_or(duration_sec);
        let t0 = (*t0).min(duration_sec);
        let t1 = next.min(duration_sec).max(t0);
        if t1 <= t0 + 1e-9 {
            continue;
        }
        sections.push(serde_json::json!({
            "t0": quantize(t0, 1e-6),
            "t1": quantize(t1, 1e-6),
            "label": format!("section_{idx}"),
        }));
    }

    if sections.is_empty() {
        sections.push(serde_json::json!({"t0": 0.0, "t1": quantize(duration_sec, 1e-6), "label": "section_0"}));
    }

    serde_json::json!({"sections_version": "1.0.0", "sections": sections})
}
#[derive(Debug, Clone)]
struct TimedMidiNote {
    t_on: f64,
    t_off: f64,
    pitch: u8,
    velocity: f64,
}

#[derive(Debug, Clone)]
struct GameplayTrackNotes {
    track_id: String,
    role: String,
    name: String,
    channel: u8,
    notes: Vec<TimedMidiNote>,
}

#[derive(Debug, Clone, Copy, Default)]
struct DrumPitchProfile {
    count: usize,
    low_ratio: f64,
    high_ratio: f64,
    zero_crossing_rate: f64,
    rms: f64,
}

#[derive(Debug, Clone)]
struct RetimedMidiTrackNote {
    t_on: f64,
    t_off: f64,
    pitch: u8,
    velocity: u8,
    channel: u8,
}

#[derive(Debug, Clone)]
struct RetimedMidiTrack {
    name: Option<Vec<u8>>,
    notes: Vec<RetimedMidiTrackNote>,
}

fn midi_bytes_to_timed_notes(midi_bytes: &[u8]) -> Result<Vec<TimedMidiNote>, String> {
    let smf = Smf::parse(midi_bytes).map_err(|e| format!("invalid midi: {e:?}"))?;
    let timing = build_midi_timing_map(&smf)?;

    #[derive(Clone, Copy, Debug)]
    struct NoteOn {
        t_ticks: u32,
        vel: u8,
    }

    let mut notes_out: Vec<TimedMidiNote> = vec![];

    for track in &smf.tracks {
        let mut t_ticks: u32 = 0;
        let mut open_notes: BTreeMap<(u8, u8), NoteOn> = BTreeMap::new();
        for ev in track {
            t_ticks = t_ticks.saturating_add(ev.delta.as_int() as u32);
            if let TrackEventKind::Midi { channel, message } = &ev.kind {
                let ch = channel.as_int() as u8;
                match message {
                    MidiMessage::NoteOn { key, vel } => {
                        let pitch = key.as_int() as u8;
                        let v = vel.as_int() as u8;
                        if v == 0 {
                            if let Some(on) = open_notes.remove(&(ch, pitch)) {
                                notes_out.push(TimedMidiNote {
                                    t_on: tick_to_sec_with_timing(on.t_ticks, &timing),
                                    t_off: tick_to_sec_with_timing(t_ticks, &timing),
                                    pitch,
                                    velocity: (on.vel as f64) / 127.0,
                                });
                            }
                        } else {
                            open_notes.insert((ch, pitch), NoteOn { t_ticks, vel: v });
                        }
                    }
                    MidiMessage::NoteOff { key, .. } => {
                        let pitch = key.as_int() as u8;
                        if let Some(on) = open_notes.remove(&(ch, pitch)) {
                            notes_out.push(TimedMidiNote {
                                t_on: tick_to_sec_with_timing(on.t_ticks, &timing),
                                t_off: tick_to_sec_with_timing(t_ticks, &timing),
                                pitch,
                                velocity: (on.vel as f64) / 127.0,
                            });
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    notes_out.sort_by(|a, b| {
        a.t_on
            .partial_cmp(&b.t_on)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.pitch.cmp(&b.pitch))
    });

    Ok(notes_out)
}

fn timed_notes_to_json(
    notes: &[TimedMidiNote],
    track_id: &str,
    source: &str,
) -> Vec<serde_json::Value> {
    notes
        .iter()
        .map(|note| {
            serde_json::json!({
                "track_id": track_id,
                "t_on": note.t_on,
                "t_off": note.t_off,
                "pitch": {"type": "midi", "value": note.pitch},
                "velocity": note.velocity,
                "confidence": 1.0,
                "source": source
            })
        })
        .collect()
}

fn shift_timed_notes(notes: &[TimedMidiNote], offset_sec: f64) -> Vec<TimedMidiNote> {
    if offset_sec <= 1e-9 {
        return notes.to_vec();
    }
    notes
        .iter()
        .map(|note| {
            let t_on = shift_time_sec(note.t_on, offset_sec);
            let shifted_t_off = shift_time_sec(note.t_off, offset_sec);
            TimedMidiNote {
                t_on,
                t_off: shifted_t_off.max(t_on + 0.01),
                pitch: note.pitch,
                velocity: note.velocity,
            }
        })
        .collect()
}

fn shift_gameplay_tracks(
    tracks: &[GameplayTrackNotes],
    offset_sec: f64,
) -> Vec<GameplayTrackNotes> {
    if offset_sec <= 1e-9 {
        return tracks.to_vec();
    }
    tracks
        .iter()
        .cloned()
        .map(|mut track| {
            track.notes = shift_timed_notes(&track.notes, offset_sec);
            track
        })
        .collect()
}

fn mono_f32_from_wav(wav: &WavPcm16) -> Vec<f32> {
    let channels = wav.channels.max(1) as usize;
    if channels == 1 {
        return wav
            .data
            .iter()
            .map(|sample| (*sample as f32) / 32768.0)
            .collect();
    }

    let total_frames = wav.data.len() / channels;
    let mut out = Vec::with_capacity(total_frames);
    for frame in 0..total_frames {
        let mut sum = 0.0f32;
        for ch in 0..channels {
            sum += (wav.data[frame * channels + ch] as f32) / 32768.0;
        }
        out.push(sum / (channels as f32));
    }
    out
}

fn analyze_drum_window_profile(samples: &[f32], sample_rate: u32) -> Option<DrumPitchProfile> {
    if samples.len() < 64 || sample_rate == 0 {
        return None;
    }

    let len = samples.len() as f64;
    let mean = samples.iter().map(|sample| *sample as f64).sum::<f64>() / len;
    let low_alpha = (-2.0 * std::f64::consts::PI * 180.0 / (sample_rate as f64)).exp();
    let bright_alpha = (-2.0 * std::f64::consts::PI * 1800.0 / (sample_rate as f64)).exp();

    let mut low_state = 0.0f64;
    let mut bright_state = 0.0f64;
    let mut total_sq = 0.0f64;
    let mut low_sq = 0.0f64;
    let mut high_sq = 0.0f64;
    let mut zero_crossings = 0usize;
    let mut prev_sign = 0i8;

    for sample in samples {
        let centered = (*sample as f64) - mean;
        total_sq += centered * centered;
        low_state = low_alpha * low_state + (1.0 - low_alpha) * centered;
        bright_state = bright_alpha * bright_state + (1.0 - bright_alpha) * centered;
        let high = centered - bright_state;
        low_sq += low_state * low_state;
        high_sq += high * high;

        let sign = if centered > 0.0 {
            1
        } else if centered < 0.0 {
            -1
        } else {
            prev_sign
        };
        if prev_sign != 0 && sign != 0 && sign != prev_sign {
            zero_crossings += 1;
        }
        prev_sign = sign;
    }

    let rms = (total_sq / len).sqrt();
    if rms <= 1e-9 {
        return None;
    }

    Some(DrumPitchProfile {
        count: 1,
        low_ratio: (low_sq / len).sqrt() / rms,
        high_ratio: (high_sq / len).sqrt() / rms,
        zero_crossing_rate: (zero_crossings as f64) / len,
        rms,
    })
}

fn weighted_profile_average<'a, I>(profiles: I) -> Option<DrumPitchProfile>
where
    I: IntoIterator<Item = &'a DrumPitchProfile>,
{
    let mut total_weight = 0.0f64;
    let mut low = 0.0f64;
    let mut high = 0.0f64;
    let mut zcr = 0.0f64;
    let mut rms = 0.0f64;
    let mut count = 0usize;

    for profile in profiles {
        let weight = profile.count.max(1) as f64;
        total_weight += weight;
        low += profile.low_ratio * weight;
        high += profile.high_ratio * weight;
        zcr += profile.zero_crossing_rate * weight;
        rms += profile.rms * weight;
        count += profile.count;
    }

    if total_weight <= 0.0 {
        return None;
    }

    Some(DrumPitchProfile {
        count,
        low_ratio: low / total_weight,
        high_ratio: high / total_weight,
        zero_crossing_rate: zcr / total_weight,
        rms: rms / total_weight,
    })
}

fn profile_for_pitches(
    profiles: &BTreeMap<u8, DrumPitchProfile>,
    pitches: &[u8],
    min_count: usize,
) -> Option<DrumPitchProfile> {
    let selected: Vec<&DrumPitchProfile> = pitches
        .iter()
        .filter_map(|pitch| profiles.get(pitch))
        .collect();
    let total = selected.iter().map(|profile| profile.count).sum::<usize>();
    if total < min_count {
        return None;
    }
    weighted_profile_average(selected)
}

fn profile_distance(a: DrumPitchProfile, b: DrumPitchProfile) -> f64 {
    let low = a.low_ratio - b.low_ratio;
    let high = a.high_ratio - b.high_ratio;
    let zcr = a.zero_crossing_rate - b.zero_crossing_rate;
    let rms = a.rms - b.rms;
    (low * low * 3.0 + high * high * 2.2 + zcr * zcr * 1.6 + rms * rms * 8.0).sqrt()
}

fn build_suno_drum_pitch_profiles(
    wav: &WavPcm16,
    notes: &[TimedMidiNote],
) -> BTreeMap<u8, DrumPitchProfile> {
    if wav.sample_rate == 0 || notes.is_empty() {
        return BTreeMap::new();
    }

    let mono = mono_f32_from_wav(wav);
    if mono.is_empty() {
        return BTreeMap::new();
    }

    let mut by_time: BTreeMap<i64, Vec<&TimedMidiNote>> = BTreeMap::new();
    for note in notes {
        let time_key = (note.t_on * 1000.0).round() as i64;
        by_time.entry(time_key).or_default().push(note);
    }

    let pre_frames = ((wav.sample_rate as f64) * 0.01).round() as usize;
    let win_frames = ((wav.sample_rate as f64) * 0.12).round() as usize;

    #[derive(Default)]
    struct Acc {
        count: usize,
        low: f64,
        high: f64,
        zcr: f64,
        rms: f64,
    }

    let mut accumulators: BTreeMap<u8, Acc> = BTreeMap::new();
    for group in by_time.values() {
        if group.len() != 1 {
            continue;
        }
        let note = group[0];
        let center_frame = (note.t_on * wav.sample_rate as f64).round().max(0.0) as usize;
        let start = center_frame.saturating_sub(pre_frames).min(mono.len());
        let end = start.saturating_add(win_frames).min(mono.len());
        if end <= start + 64 {
            continue;
        }
        let Some(profile) = analyze_drum_window_profile(&mono[start..end], wav.sample_rate) else {
            continue;
        };
        let acc = accumulators.entry(note.pitch).or_default();
        acc.count += 1;
        acc.low += profile.low_ratio;
        acc.high += profile.high_ratio;
        acc.zcr += profile.zero_crossing_rate;
        acc.rms += profile.rms;
    }

    let mut out = BTreeMap::new();
    for (pitch, acc) in accumulators {
        if acc.count == 0 {
            continue;
        }
        let denom = acc.count as f64;
        out.insert(
            pitch,
            DrumPitchProfile {
                count: acc.count,
                low_ratio: acc.low / denom,
                high_ratio: acc.high / denom,
                zero_crossing_rate: acc.zcr / denom,
                rms: acc.rms / denom,
            },
        );
    }
    out
}

fn infer_suno_drum_pitch_mapping_from_profiles(
    profiles: &BTreeMap<u8, DrumPitchProfile>,
) -> BTreeMap<u8, u8> {
    let mut mapping = BTreeMap::new();
    let kick = profile_for_pitches(profiles, &[35, 36], 24);
    let snare = profile_for_pitches(profiles, &[37, 38, 39, 40], 24);
    let hat = profile_for_pitches(profiles, &[42, 44, 46], 12);
    let cym = profile_for_pitches(profiles, &[49, 52, 55, 57], 12);

    for pitch in [32u8, 34, 41, 43, 45, 47, 48, 50, 58] {
        let Some(profile) = profiles.get(&pitch).copied() else {
            continue;
        };
        if profile.count < 8 {
            continue;
        }

        let mut candidates: Vec<(u8, f64)> = vec![];
        if let Some(anchor) = kick {
            candidates.push((36, profile_distance(profile, anchor)));
        }
        if let Some(anchor) = snare {
            candidates.push((38, profile_distance(profile, anchor)));
        }
        if let Some(anchor) = hat {
            candidates.push((46, profile_distance(profile, anchor)));
        }
        if let Some(anchor) = cym {
            candidates.push((49, profile_distance(profile, anchor)));
        }
        if candidates.len() < 2 {
            continue;
        }
        candidates.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
        let (best_pitch, best_dist) = candidates[0];
        let second_dist = candidates[1].1;
        let margin = second_dist - best_dist;

        match best_pitch {
            36 if margin >= 0.01 && profile.low_ratio >= 0.13 && profile.count >= 12 => {
                mapping.insert(pitch, 36);
            }
            38 if margin >= 0.01 && profile.high_ratio < 0.45 && profile.count >= 12 => {
                mapping.insert(pitch, 38);
            }
            46 if margin >= 0.01 && profile.high_ratio >= 0.42 && profile.count >= 10 => {
                mapping.insert(pitch, 46);
            }
            49 if margin >= 0.01 && profile.high_ratio >= 0.34 && profile.count >= 8 => {
                mapping.insert(pitch, 49);
            }
            _ => {}
        }
    }

    mapping
}

fn remap_suno_drum_notes(
    scan: &RawSongFolderScan,
    notes: &[TimedMidiNote],
) -> Result<(Vec<TimedMidiNote>, BTreeMap<u8, u8>), String> {
    let Some(stem_part) = best_part_for_game_role(&scan.stem_parts, "drums") else {
        return Ok((notes.to_vec(), BTreeMap::new()));
    };

    let wav = read_wav_pcm16(&stem_part.path)?;
    let profiles = build_suno_drum_pitch_profiles(&wav, notes);
    let mapping = infer_suno_drum_pitch_mapping_from_profiles(&profiles);
    if mapping.is_empty() {
        return Ok((notes.to_vec(), mapping));
    }

    let mut remapped: Vec<TimedMidiNote> = notes
        .iter()
        .cloned()
        .map(|mut note| {
            if let Some(mapped_pitch) = mapping.get(&note.pitch) {
                note.pitch = *mapped_pitch;
            }
            note
        })
        .collect();

    remapped.sort_by(|a, b| {
        a.t_on
            .partial_cmp(&b.t_on)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.pitch.cmp(&b.pitch))
            .then_with(|| {
                a.t_off
                    .partial_cmp(&b.t_off)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
    });

    let mut deduped: Vec<TimedMidiNote> = Vec::with_capacity(remapped.len());
    for note in remapped {
        if let Some(last) = deduped.last_mut() {
            if last.pitch == note.pitch
                && (last.t_on - note.t_on).abs() < 1e-6
                && (last.t_off - note.t_off).abs() < 1e-6
            {
                last.velocity = last.velocity.max(note.velocity);
                continue;
            }
        }
        deduped.push(note);
    }

    Ok((deduped, mapping))
}

fn extract_retimed_midi_tracks(
    midi_bytes: &[u8],
) -> Result<(Vec<RetimedMidiTrack>, MidiTimingMap), String> {
    let smf = Smf::parse(midi_bytes).map_err(|e| format!("invalid midi: {e:?}"))?;
    let timing = build_midi_timing_map(&smf)?;

    #[derive(Clone, Copy, Debug)]
    struct NoteOn {
        t_ticks: u32,
        vel: u8,
    }

    let mut tracks_out: Vec<RetimedMidiTrack> = vec![];
    for track in &smf.tracks {
        let mut t_ticks: u32 = 0;
        let mut open_notes: BTreeMap<(u8, u8), NoteOn> = BTreeMap::new();
        let mut track_name: Option<Vec<u8>> = None;
        let mut notes: Vec<RetimedMidiTrackNote> = vec![];

        for ev in track {
            t_ticks = t_ticks.saturating_add(ev.delta.as_int() as u32);
            match &ev.kind {
                TrackEventKind::Meta(MetaMessage::TrackName(name)) => {
                    if track_name.is_none() {
                        track_name = Some(name.to_vec());
                    }
                }
                TrackEventKind::Midi { channel, message } => {
                    let ch = channel.as_int() as u8;
                    match message {
                        MidiMessage::NoteOn { key, vel } => {
                            let pitch = key.as_int() as u8;
                            let v = vel.as_int() as u8;
                            if v == 0 {
                                if let Some(on) = open_notes.remove(&(ch, pitch)) {
                                    notes.push(RetimedMidiTrackNote {
                                        t_on: tick_to_sec_with_timing(on.t_ticks, &timing),
                                        t_off: tick_to_sec_with_timing(t_ticks, &timing),
                                        pitch,
                                        velocity: on.vel.max(1),
                                        channel: ch,
                                    });
                                }
                            } else {
                                open_notes.insert((ch, pitch), NoteOn { t_ticks, vel: v });
                            }
                        }
                        MidiMessage::NoteOff { key, .. } => {
                            let pitch = key.as_int() as u8;
                            if let Some(on) = open_notes.remove(&(ch, pitch)) {
                                notes.push(RetimedMidiTrackNote {
                                    t_on: tick_to_sec_with_timing(on.t_ticks, &timing),
                                    t_off: tick_to_sec_with_timing(t_ticks, &timing),
                                    pitch,
                                    velocity: on.vel.max(1),
                                    channel: ch,
                                });
                            }
                        }
                        _ => {}
                    }
                }
                _ => {}
            }
        }

        if !notes.is_empty() || track_name.is_some() {
            notes.sort_by(|a, b| {
                a.t_on
                    .partial_cmp(&b.t_on)
                    .unwrap_or(std::cmp::Ordering::Equal)
                    .then_with(|| a.channel.cmp(&b.channel))
                    .then_with(|| a.pitch.cmp(&b.pitch))
            });
            tracks_out.push(RetimedMidiTrack {
                name: track_name,
                notes,
            });
        }
    }

    Ok((tracks_out, timing))
}

fn write_retimed_source_midi(dst: &Path, midi_bytes: &[u8], offset_sec: f64) -> Result<(), String> {
    let (tracks, timing) = extract_retimed_midi_tracks(midi_bytes)?;
    let tpq = timing.tpq.max(1);

    let mut midi_tracks: Vec<Vec<TrackEvent<'_>>> = vec![];
    let mut conductor_abs_events: Vec<(u32, u8, TrackEventKind<'_>)> = vec![];
    for segment in &timing.tempo_segments {
        conductor_abs_events.push((
            segment.tick,
            0,
            TrackEventKind::Meta(MetaMessage::Tempo(u24::new(segment.us_per_quarter))),
        ));
    }
    for sig in &timing.time_signatures {
        conductor_abs_events.push((
            sig.tick,
            1,
            TrackEventKind::Meta(MetaMessage::TimeSignature(
                sig.numerator,
                sig.denominator_pow,
                sig.clocks_per_click,
                sig.notated_32nds_per_beat,
            )),
        ));
    }
    conductor_abs_events.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));

    let mut conductor_track: Vec<TrackEvent<'_>> = vec![];
    let mut conductor_last_tick = 0u32;
    for (tick, _, kind) in conductor_abs_events {
        let delta = tick.saturating_sub(conductor_last_tick);
        conductor_last_tick = tick;
        conductor_track.push(TrackEvent {
            delta: u28::new(delta),
            kind,
        });
    }
    conductor_track.push(TrackEvent {
        delta: u28::new(0),
        kind: TrackEventKind::Meta(MetaMessage::EndOfTrack),
    });
    midi_tracks.push(conductor_track);

    for track_spec in &tracks {
        let mut track: Vec<TrackEvent<'_>> = vec![];
        if let Some(name) = track_spec.name.as_ref() {
            track.push(TrackEvent {
                delta: u28::new(0),
                kind: TrackEventKind::Meta(MetaMessage::TrackName(name.as_slice())),
            });
        }

        let mut abs_events: Vec<(u32, bool, u8, u8, u8)> = vec![];
        for note in &track_spec.notes {
            let shifted_t_on = shift_time_sec(note.t_on, offset_sec);
            let shifted_t_off = shift_time_sec(note.t_off, offset_sec).max(shifted_t_on + 0.01);
            let t_on = sec_to_tick_with_timing(shifted_t_on, &timing);
            let mut t_off = sec_to_tick_with_timing(shifted_t_off, &timing);
            if t_off <= t_on {
                t_off = t_on + 1;
            }
            abs_events.push((t_on, true, note.channel, note.pitch, note.velocity.max(1)));
            abs_events.push((t_off, false, note.channel, note.pitch, 0));
        }
        abs_events.sort_by(|a, b| {
            a.0.cmp(&b.0)
                .then_with(|| a.1.cmp(&b.1))
                .then_with(|| a.2.cmp(&b.2))
                .then_with(|| a.3.cmp(&b.3))
        });

        let mut last_tick = 0u32;
        for (tick, is_note_on, channel, pitch, velocity) in abs_events {
            let delta = tick.saturating_sub(last_tick);
            last_tick = tick;
            let kind = if is_note_on {
                TrackEventKind::Midi {
                    channel: u4::new(channel),
                    message: MidiMessage::NoteOn {
                        key: u7::new(pitch),
                        vel: u7::new(velocity),
                    },
                }
            } else {
                TrackEventKind::Midi {
                    channel: u4::new(channel),
                    message: MidiMessage::NoteOff {
                        key: u7::new(pitch),
                        vel: u7::new(0),
                    },
                }
            };
            track.push(TrackEvent {
                delta: u28::new(delta),
                kind,
            });
        }

        track.push(TrackEvent {
            delta: u28::new(0),
            kind: TrackEventKind::Meta(MetaMessage::EndOfTrack),
        });
        midi_tracks.push(track);
    }

    let smf = Smf {
        header: Header::new(Format::Parallel, Timing::Metrical(u15::new(tpq))),
        tracks: midi_tracks,
    };
    let mut out = vec![];
    smf.write_std(&mut out)
        .map_err(|e| format!("encode retimed notes.mid: {e}"))?;
    fs::write(dst, out).map_err(|e| format!("write {}: {e}", dst.display()))?;
    Ok(())
}

fn shift_beats_json(beats: &mut serde_json::Value, offset_sec: f64) {
    if offset_sec <= 1e-9 {
        return;
    }
    if let Some(items) = beats
        .get_mut("beats")
        .and_then(|value| value.as_array_mut())
    {
        for beat in items {
            let t = beat
                .get("t")
                .and_then(|value| value.as_f64())
                .unwrap_or(0.0);
            if let Some(obj) = beat.as_object_mut() {
                obj.insert(
                    "t".to_string(),
                    serde_json::json!(shift_time_sec(t, offset_sec)),
                );
            }
        }
    }
}

fn shift_tempo_map_json(tempo: &mut serde_json::Value, offset_sec: f64) {
    if offset_sec <= 1e-9 {
        return;
    }
    if let Some(items) = tempo
        .get_mut("segments")
        .and_then(|value| value.as_array_mut())
    {
        for segment in items {
            let t0 = segment
                .get("t0")
                .and_then(|value| value.as_f64())
                .unwrap_or(0.0);
            if let Some(obj) = segment.as_object_mut() {
                obj.insert(
                    "t0".to_string(),
                    serde_json::json!(shift_time_sec(t0, offset_sec)),
                );
            }
        }
    }
}

fn preferred_detected_roles_for_sync_estimate(role: &str) -> &'static [&'static str] {
    match role {
        "drums" => &["drums"],
        "bass" => &["bass"],
        "lead_guitar" => &["lead_guitar", "guitar"],
        "rhythm_guitar" => &["rhythm_guitar", "guitar"],
        "keys" => &["keys"],
        "vocals" => &["vocals"],
        _ => &[],
    }
}

fn best_part_for_game_role<'a>(
    parts: &'a [DetectedSongPartScan],
    role: &str,
) -> Option<&'a DetectedSongPartScan> {
    let preferred = preferred_detected_roles_for_sync_estimate(role);
    parts
        .iter()
        .filter(|part| part.game_role.as_deref() == Some(role))
        .min_by_key(|part| {
            preferred
                .iter()
                .position(|preferred_role| *preferred_role == part.detected_role)
                .unwrap_or(preferred.len())
        })
}

fn observed_audio_midi_start_delta_sec(
    scan: &RawSongFolderScan,
    role: &str,
) -> Result<Option<f64>, String> {
    let Some(stem_part) = best_part_for_game_role(&scan.stem_parts, role) else {
        return Ok(None);
    };
    let Some(midi_part) = best_part_for_game_role(&scan.midi_parts, role) else {
        return Ok(None);
    };

    let wav = read_wav_pcm16(&stem_part.path)?;
    let Some(audio_start_sec) = first_active_segment_start_sec(&wav) else {
        return Ok(None);
    };
    let midi_bytes = fs::read(&midi_part.path)
        .map_err(|e| format!("read midi {}: {e}", midi_part.path.display()))?;
    let Some(midi_start_sec) = first_midi_note_start_sec(&midi_bytes)? else {
        return Ok(None);
    };
    Ok(Some(quantize(midi_start_sec - audio_start_sec, 1e-6)))
}

fn median(values: &mut [f64]) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mid = values.len() / 2;
    if values.len().is_multiple_of(2) {
        Some((values[mid - 1] + values[mid]) * 0.5)
    } else {
        Some(values[mid])
    }
}

fn estimate_audio_start_offset_sec(
    scan: &RawSongFolderScan,
) -> Result<Option<(f64, usize)>, String> {
    let mut candidates: Vec<f64> = vec![];
    for role in [
        "drums",
        "bass",
        "lead_guitar",
        "rhythm_guitar",
        "keys",
        "vocals",
    ] {
        let Some(delta) = observed_audio_midi_start_delta_sec(scan, role)? else {
            continue;
        };
        if (0.05..=2.0).contains(&delta.abs()) {
            candidates.push(delta);
        }
    }

    let candidate_count = candidates.len();
    let Some(offset_sec) = median(&mut candidates) else {
        return Ok(None);
    };
    Ok(Some((quantize(offset_sec, 1e-6), candidate_count)))
}

fn resolve_role_normalization_offset_sec(
    scan: &RawSongFolderScan,
    role: &str,
    global_offset_sec: f64,
    global_pair_count: usize,
    warnings: &mut Vec<String>,
) -> Result<Option<f64>, String> {
    match observed_audio_midi_start_delta_sec(scan, role)? {
        Some(delta_sec) if delta_sec.abs() > 2.0 => {
            if global_pair_count > 0 {
                warnings.push(format!(
                    "{} source MIDI timing differed by {:.3}s from its audio stem; using the cross-track median correction of {:.3}s instead.",
                    gameplay_role_label(role),
                    delta_sec.abs(),
                    global_offset_sec.abs()
                ));
                Ok(Some(global_offset_sec))
            } else {
                warnings.push(format!(
                    "{} source MIDI timing differed by {:.3}s from its audio stem and was excluded from the auto-normalized gameplay chart.",
                    gameplay_role_label(role),
                    delta_sec.abs()
                ));
                Ok(None)
            }
        }
        Some(delta_sec) if delta_sec.abs() >= 0.05 => Ok(Some(delta_sec)),
        Some(_) => Ok(Some(0.0)),
        None if global_pair_count > 0 => Ok(Some(global_offset_sec)),
        None => Ok(Some(0.0)),
    }
}

fn collect_source_midi_audio_sync_warnings(
    scan: &RawSongFolderScan,
) -> Result<Vec<String>, String> {
    let mut warnings: Vec<String> = vec![];
    for role in [
        "drums",
        "bass",
        "lead_guitar",
        "rhythm_guitar",
        "keys",
        "vocals",
    ] {
        let Some(stem_part) = best_part_for_game_role(&scan.stem_parts, role) else {
            continue;
        };
        let Some(midi_part) = best_part_for_game_role(&scan.midi_parts, role) else {
            continue;
        };

        let wav = read_wav_pcm16(&stem_part.path)?;
        let Some(audio_start_sec) = first_active_segment_start_sec(&wav) else {
            continue;
        };
        let midi_bytes = fs::read(&midi_part.path)
            .map_err(|e| format!("read midi {}: {e}", midi_part.path.display()))?;
        let Some(midi_start_sec) = first_midi_note_start_sec(&midi_bytes)? else {
            continue;
        };

        let delta_sec = quantize(midi_start_sec - audio_start_sec, 1e-6);
        if delta_sec.abs() < 0.150 {
            continue;
        }

        let direction = if delta_sec > 0.0 { "later" } else { "earlier" };
        warnings.push(format!(
            "{} source MIDI starts {:.3}s {} than its audio stem: {} first audio onset at {:.3}s, {} first MIDI note at {:.3}s. Studio can normalize this during import.",
            gameplay_role_label(role),
            delta_sec.abs(),
            direction,
            path_display_name(&stem_part.path),
            audio_start_sec,
            path_display_name(&midi_part.path),
            midi_start_sec,
        ));
    }
    Ok(warnings)
}

fn timing_priority(timing: &MidiTimingMap) -> (usize, usize) {
    (timing.tempo_segments.len(), timing.time_signatures.len())
}

fn gameplay_role_from_track_name_bytes(name: &[u8]) -> Option<String> {
    let tokens = String::from_utf8_lossy(name)
        .split(|ch: char| !ch.is_ascii_alphanumeric())
        .filter(|part| !part.is_empty())
        .map(|part| part.to_ascii_lowercase())
        .collect::<Vec<_>>();
    let detected_role = detect_part_role_from_tokens(&tokens, false);
    map_detected_role_to_game_role(&detected_role).map(|role| role.to_string())
}

fn timed_note_from_retimed_note(note: &RetimedMidiTrackNote) -> TimedMidiNote {
    TimedMidiNote {
        t_on: note.t_on,
        t_off: note.t_off.max(note.t_on + 0.01),
        pitch: note.pitch,
        velocity: (note.velocity as f64) / 127.0,
    }
}

fn append_gameplay_notes_for_role(
    by_role: &mut BTreeMap<String, GameplayTrackNotes>,
    role: &str,
    notes: Vec<TimedMidiNote>,
) {
    if notes.is_empty() {
        return;
    }

    let entry = by_role
        .entry(role.to_string())
        .or_insert_with(|| GameplayTrackNotes {
            track_id: role.to_string(),
            role: role.to_string(),
            name: canonical_track_name_for_role(role).to_string(),
            channel: canonical_channel_for_role(role),
            notes: vec![],
        });
    entry.notes.extend(notes);
}

fn extract_gameplay_tracks_from_midi_bytes(
    midi_bytes: &[u8],
) -> Result<(Vec<GameplayTrackNotes>, MidiTimingMap), String> {
    let (retimed_tracks, timing) = extract_retimed_midi_tracks(midi_bytes)?;
    let mut by_role: BTreeMap<String, GameplayTrackNotes> = BTreeMap::new();

    for track in retimed_tracks {
        if let Some(role) = track
            .name
            .as_deref()
            .and_then(gameplay_role_from_track_name_bytes)
        {
            let notes = track
                .notes
                .iter()
                .map(timed_note_from_retimed_note)
                .collect::<Vec<_>>();
            append_gameplay_notes_for_role(&mut by_role, &role, notes);
            continue;
        }

        let mut notes_by_role: BTreeMap<String, Vec<TimedMidiNote>> = BTreeMap::new();
        for note in &track.notes {
            let Some(role) = gameplay_role_for_midi_channel(note.channel) else {
                continue;
            };
            notes_by_role
                .entry(role.to_string())
                .or_default()
                .push(timed_note_from_retimed_note(note));
        }

        for (role, notes) in notes_by_role {
            append_gameplay_notes_for_role(&mut by_role, &role, notes);
        }
    }

    let mut tracks: Vec<GameplayTrackNotes> = by_role.into_values().collect();
    for track in &mut tracks {
        track.notes.sort_by(|a, b| {
            a.t_on
                .partial_cmp(&b.t_on)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.pitch.cmp(&b.pitch))
        });
    }
    tracks.sort_by_key(|track| gameplay_role_sort_key(&track.role));
    Ok((tracks, timing))
}

fn build_combined_gameplay_tracks(
    scan: &RawSongFolderScan,
) -> Result<(Vec<GameplayTrackNotes>, Option<MidiTimingMap>), String> {
    let mut by_role: BTreeMap<String, GameplayTrackNotes> = BTreeMap::new();
    let mut canonical_timing: Option<MidiTimingMap> = None;

    for midi_path in &scan.midi_files {
        let bytes =
            fs::read(midi_path).map_err(|e| format!("read midi {}: {e}", midi_path.display()))?;
        let (tracks, timing) = extract_gameplay_tracks_from_midi_bytes(&bytes)?;
        if canonical_timing
            .as_ref()
            .map(|existing| timing_priority(&timing) > timing_priority(existing))
            .unwrap_or(true)
        {
            canonical_timing = Some(timing.clone());
        }

        for track in tracks {
            let entry = by_role
                .entry(track.role.clone())
                .or_insert_with(|| GameplayTrackNotes {
                    track_id: track.track_id.clone(),
                    role: track.role.clone(),
                    name: track.name.clone(),
                    channel: track.channel,
                    notes: vec![],
                });
            entry.notes.extend(track.notes);
        }
    }

    let mut tracks: Vec<GameplayTrackNotes> = by_role.into_values().collect();
    for track in &mut tracks {
        track.notes.sort_by(|a, b| {
            a.t_on
                .partial_cmp(&b.t_on)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.pitch.cmp(&b.pitch))
        });
    }
    tracks.sort_by_key(|track| gameplay_role_sort_key(&track.role));
    Ok((tracks, canonical_timing))
}

fn write_combined_gameplay_midi(
    dst: &Path,
    tracks: &[GameplayTrackNotes],
    timing: &MidiTimingMap,
) -> Result<(), String> {
    if tracks.is_empty() {
        return Err("no mapped MIDI tracks to write".to_string());
    }

    let tpq = timing.tpq.max(1);
    let track_name_bytes: Vec<Vec<u8>> = tracks
        .iter()
        .map(|track| track.name.as_bytes().to_vec())
        .collect();
    let mut midi_tracks: Vec<Vec<TrackEvent<'_>>> = vec![];

    let mut conductor_abs_events: Vec<(u32, u8, TrackEventKind<'_>)> = vec![];
    for segment in &timing.tempo_segments {
        conductor_abs_events.push((
            segment.tick,
            0,
            TrackEventKind::Meta(MetaMessage::Tempo(u24::new(segment.us_per_quarter))),
        ));
    }
    for sig in &timing.time_signatures {
        conductor_abs_events.push((
            sig.tick,
            1,
            TrackEventKind::Meta(MetaMessage::TimeSignature(
                sig.numerator,
                sig.denominator_pow,
                sig.clocks_per_click,
                sig.notated_32nds_per_beat,
            )),
        ));
    }
    conductor_abs_events.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));

    let mut conductor_track: Vec<TrackEvent<'_>> = vec![];
    let mut conductor_last_tick = 0u32;
    for (tick, _, kind) in conductor_abs_events {
        let delta = tick.saturating_sub(conductor_last_tick);
        conductor_last_tick = tick;
        conductor_track.push(TrackEvent {
            delta: u28::new(delta),
            kind,
        });
    }
    conductor_track.push(TrackEvent {
        delta: u28::new(0),
        kind: TrackEventKind::Meta(MetaMessage::EndOfTrack),
    });
    midi_tracks.push(conductor_track);

    for (idx, track_spec) in tracks.iter().enumerate() {
        let mut abs_events: Vec<(u32, bool, u8, u8)> = vec![];
        for note in &track_spec.notes {
            let t_on = sec_to_tick_with_timing(note.t_on, timing);
            let mut t_off = sec_to_tick_with_timing(note.t_off.max(note.t_on + 0.01), timing);
            if t_off <= t_on {
                t_off = t_on + 1;
            }
            let velocity = ((note.velocity.clamp(0.0, 1.0) * 127.0).round() as u8).max(1);
            abs_events.push((t_on, true, note.pitch, velocity));
            abs_events.push((t_off, false, note.pitch, 0));
        }
        abs_events.sort_by(|a, b| {
            a.0.cmp(&b.0)
                .then_with(|| a.1.cmp(&b.1))
                .then_with(|| a.2.cmp(&b.2))
        });

        let mut track: Vec<TrackEvent<'_>> = vec![TrackEvent {
            delta: u28::new(0),
            kind: TrackEventKind::Meta(MetaMessage::TrackName(track_name_bytes[idx].as_slice())),
        }];

        let mut last_tick = 0u32;
        for (tick, is_note_on, pitch, velocity) in abs_events {
            let delta = tick.saturating_sub(last_tick);
            last_tick = tick;
            let kind = if is_note_on {
                TrackEventKind::Midi {
                    channel: u4::new(track_spec.channel),
                    message: MidiMessage::NoteOn {
                        key: u7::new(pitch),
                        vel: u7::new(velocity),
                    },
                }
            } else {
                TrackEventKind::Midi {
                    channel: u4::new(track_spec.channel),
                    message: MidiMessage::NoteOff {
                        key: u7::new(pitch),
                        vel: u7::new(0),
                    },
                }
            };
            track.push(TrackEvent {
                delta: u28::new(delta),
                kind,
            });
        }
        track.push(TrackEvent {
            delta: u28::new(0),
            kind: TrackEventKind::Meta(MetaMessage::EndOfTrack),
        });
        midi_tracks.push(track);
    }

    let smf = Smf {
        header: Header::new(Format::Parallel, Timing::Metrical(u15::new(tpq))),
        tracks: midi_tracks,
    };
    let mut out = vec![];
    smf.write_std(&mut out)
        .map_err(|e| format!("encode combined notes.mid: {e}"))?;
    fs::write(dst, out).map_err(|e| format!("write {}: {e}", dst.display()))?;
    Ok(())
}

fn find_karaoke_json(root: &Path) -> Option<PathBuf> {
    // Look for lyrics_karaoke.karaoke.json (produced by PsalmsKaraoke) anywhere under root.
    // Prefer a path containing "newoutput" if multiple are present.
    fn rec(dir: &Path, depth: usize, out: &mut Vec<PathBuf>) {
        if depth > 6 {
            return;
        }
        let Ok(rd) = fs::read_dir(dir) else {
            return;
        };
        for e in rd.flatten() {
            let p = e.path();
            if p.is_dir() {
                rec(&p, depth + 1, out);
            } else if p.is_file() {
                if let Some(name) = p.file_name().and_then(|s| s.to_str()) {
                    if name == "lyrics_karaoke.karaoke.json" {
                        out.push(p);
                    }
                }
            }
        }
    }

    let mut matches: Vec<PathBuf> = vec![];
    rec(root, 0, &mut matches);
    if matches.is_empty() {
        return None;
    }

    matches.sort();
    for p in &matches {
        if p.to_string_lossy()
            .to_ascii_lowercase()
            .contains("newoutput")
        {
            return Some(p.clone());
        }
    }
    Some(matches[0].clone())
}

fn find_root_lyrics_txt(root: &Path) -> Option<PathBuf> {
    let Ok(rd) = fs::read_dir(root) else {
        return None;
    };
    for entry in rd.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let name = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
        if name.eq_ignore_ascii_case("lyrics.txt") {
            return Some(path);
        }
    }
    None
}

fn scan_raw_song_folder(root: &Path) -> Result<RawSongFolderScan, String> {
    if !root.is_dir() {
        return Err(format!("folder not found: {}", root.display()));
    }

    let title_guess = root
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("Raw Song")
        .to_string();

    let mut wavs: Vec<PathBuf> = vec![];
    let mut midis: Vec<PathBuf> = vec![];
    let rd = fs::read_dir(root).map_err(|e| format!("cannot read_dir: {}: {e}", root.display()))?;
    for entry in rd.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let ext = path
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if ext == "wav" {
            wavs.push(path);
        } else if ext == "mid" || ext == "midi" {
            midis.push(path);
        }
    }
    wavs.sort();
    midis.sort();

    if wavs.is_empty() {
        return Err("no WAV files found in folder".to_string());
    }
    if midis.is_empty() {
        return Err("no MIDI files found in folder".to_string());
    }

    let all_stem_parts: Vec<DetectedSongPartScan> =
        wavs.iter().map(|path| detect_stem_part(path)).collect();
    let mix_wav = all_stem_parts
        .iter()
        .find(|part| part.detected_role == "mix")
        .map(|part| part.path.clone());
    let mut stem_parts: Vec<DetectedSongPartScan> = all_stem_parts
        .iter()
        .filter(|part| part.detected_role != "mix")
        .cloned()
        .collect();
    let mut stem_wavs: Vec<PathBuf> = stem_parts.iter().map(|part| part.path.clone()).collect();
    let midi_parts: Vec<DetectedSongPartScan> =
        midis.iter().map(|path| detect_midi_part(path)).collect();

    let mut warnings: Vec<String> = vec![];
    if stem_wavs.is_empty() {
        stem_wavs = wavs.clone();
        stem_parts = stem_wavs
            .iter()
            .map(|path| detect_stem_part(path))
            .collect();
        warnings
            .push("No non-mix stem WAVs detected; using all WAV files as stem inputs.".to_string());
    } else if let Some(mix_path) = &mix_wav {
        warnings.push(format!(
            "Using {} as the mix audio and excluding it from stem summing.",
            path_display_name(mix_path)
        ));
    }

    if stem_wavs.len() == 1 {
        warnings.push(format!(
            "Only one stem WAV detected ({}). Import will still work, but check that the folder contains the intended stem export.",
            path_display_name(&stem_wavs[0])
        ));
    }

    let vocal_stem = stem_parts
        .iter()
        .find(|part| part.detected_role == "vocals")
        .or_else(|| {
            stem_parts
                .iter()
                .find(|part| part.detected_role == "backing_vocals")
        })
        .map(|part| part.path.clone())
        .or_else(|| {
            stem_wavs
                .iter()
                .find(|path| looks_like_vocal_wav(path))
                .cloned()
        });
    let lyrics_txt = find_root_lyrics_txt(root);
    let karaoke_json = find_karaoke_json(root);

    if lyrics_txt.is_some() && karaoke_json.is_none() && vocal_stem.is_none() && mix_wav.is_none() {
        warnings.push("lyrics.txt was found, but there is no obvious vocals stem; lyric timing will fall back to the mixed audio or a uniform split.".to_string());
    }

    let mut stem_role_files: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for part in &stem_parts {
        if let Some(role) = &part.game_role {
            stem_role_files
                .entry(role.clone())
                .or_default()
                .push(path_display_name(&part.path));
        }
    }

    let mut midi_role_files: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for part in &midi_parts {
        if let Some(role) = &part.game_role {
            midi_role_files
                .entry(role.clone())
                .or_default()
                .push(path_display_name(&part.path));
        }
    }

    for (role, files) in &midi_role_files {
        if files.len() > 1 {
            warnings.push(format!(
                "Merging {} MIDI files into the {} gameplay track: {}.",
                files.len(),
                gameplay_role_label(role),
                files.join(", ")
            ));
        }
    }

    for role in stem_role_files.keys() {
        if !midi_role_files.contains_key(role) {
            warnings.push(format!(
                "Detected {} audio stem(s) for {}, but no matching MIDI file.",
                stem_role_files
                    .get(role)
                    .map(|files| files.len())
                    .unwrap_or(0),
                gameplay_role_label(role)
            ));
        }
    }

    for role in midi_role_files.keys() {
        if !stem_role_files.contains_key(role) && role != "vocals" {
            warnings.push(format!(
                "Detected MIDI for {}, but no matching audio stem was identified.",
                gameplay_role_label(role)
            ));
        }
    }

    if midi_parts.iter().all(|part| part.game_role.is_none()) {
        warnings.push(
            "No MIDI files were recognized as drums, bass, guitar, keys, or vocals; in-game instrument mapping may be limited."
                .to_string(),
        );
    }

    let midi_chart_ready = midi_parts.iter().any(|part| part.game_role.is_some());
    let mut scan = RawSongFolderScan {
        folder_path: root.to_path_buf(),
        title_guess,
        stem_wavs,
        midi_files: midis,
        stem_parts,
        midi_parts,
        lyrics_txt,
        karaoke_json,
        vocal_stem,
        mix_wav,
        mapped_game_roles: vec![],
        midi_chart_ready,
        source_midi_offset_sec: None,
        source_midi_offset_pair_count: 0,
        warnings,
    };
    scan.warnings
        .extend(collect_source_midi_audio_sync_warnings(&scan)?);
    if let Some((offset_sec, pair_count)) = estimate_audio_start_offset_sec(&scan)? {
        scan.source_midi_offset_sec = Some(offset_sec);
        scan.source_midi_offset_pair_count = pair_count;
    }
    let (mapped_tracks, _) = build_combined_gameplay_tracks(&scan)?;
    let mut mapped_roles: BTreeSet<String> =
        mapped_tracks.into_iter().map(|track| track.role).collect();
    if scan.lyrics_txt.is_some() || scan.karaoke_json.is_some() || scan.vocal_stem.is_some() {
        mapped_roles.insert("vocals".to_string());
    }
    scan.mapped_game_roles = mapped_roles.into_iter().collect();
    scan.midi_chart_ready = !scan.mapped_game_roles.is_empty();
    Ok(scan)
}

pub fn inspect_raw_song_folder(folder_path: &Path) -> Result<RawSongFolderInspection, String> {
    let scan = scan_raw_song_folder(folder_path)?;
    Ok(RawSongFolderInspection {
        folder_path: scan.folder_path.to_string_lossy().to_string(),
        title_guess: scan.title_guess,
        stem_wav_paths: scan
            .stem_wavs
            .iter()
            .map(|path| path.to_string_lossy().to_string())
            .collect(),
        midi_paths: scan
            .midi_files
            .iter()
            .map(|path| path.to_string_lossy().to_string())
            .collect(),
        stem_parts: scan.stem_parts.iter().map(export_detected_part).collect(),
        midi_parts: scan.midi_parts.iter().map(export_detected_part).collect(),
        lyrics_txt_path: scan
            .lyrics_txt
            .as_ref()
            .map(|path| path.to_string_lossy().to_string()),
        karaoke_json_path: scan
            .karaoke_json
            .as_ref()
            .map(|path| path.to_string_lossy().to_string()),
        vocal_stem_path: scan
            .vocal_stem
            .as_ref()
            .map(|path| path.to_string_lossy().to_string()),
        mix_wav_path: scan
            .mix_wav
            .as_ref()
            .map(|path| path.to_string_lossy().to_string()),
        mapped_game_roles: scan.mapped_game_roles,
        midi_chart_ready: scan.midi_chart_ready,
        source_midi_offset_sec: scan.source_midi_offset_sec,
        source_midi_offset_pair_count: scan.source_midi_offset_pair_count,
        warnings: scan.warnings,
    })
}

fn pad_wavs_to_max_len(wavs: &mut [WavPcm16]) {
    if wavs.is_empty() {
        return;
    }
    let max_len = wavs.iter().map(|w| w.data.len()).max().unwrap_or(0);
    for w in wavs {
        if w.data.len() < max_len {
            w.data.resize(max_len, 0);
        }
    }
}

pub fn import_raw_song_folder(
    req: ImportRawSongFolderRequest,
    songs_folder: &Path,
) -> Result<ImportRawSongFolderResult, String> {
    let root = PathBuf::from(&req.folder_path);
    let scan = scan_raw_song_folder(&root)?;

    // Read + validate stems.
    let mixed = if let Some(mix_path) = &scan.mix_wav {
        read_wav_pcm16(mix_path)?
    } else {
        let mut wavs: Vec<WavPcm16> = vec![];
        for stem_path in &scan.stem_wavs {
            wavs.push(read_wav_pcm16(stem_path)?);
        }
        pad_wavs_to_max_len(&mut wavs);
        if wavs.len() == 1 {
            wavs.remove(0)
        } else {
            mix_wavs(&wavs)?
        }
    };

    // Derive defaults.
    let title = req
        .title
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| scan.title_guess.clone());
    let artist = req
        .artist
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "".to_string());

    // Build output folder name.
    let base = format!("{}_{}", sanitize_id(&artist), sanitize_id(&title));
    let base = base.trim_matches('_');
    let mut out_dir = songs_folder.join(format!("raw_{base}.songpack"));
    if out_dir.exists() {
        for i in 2..=9999 {
            let candidate = songs_folder.join(format!("raw_{base}_{i}.songpack"));
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
    fs::create_dir_all(out_dir.join("features").join("midi"))
        .map_err(|e| format!("mkdir features/midi: {e}"))?;
    fs::create_dir_all(out_dir.join("charts")).map_err(|e| format!("mkdir charts: {e}"))?;

    let mix_path = out_dir.join("audio").join("mix.wav");
    write_wav_pcm16(&mix_path, &mixed)?;

    let mut warnings = scan.warnings.clone();
    let duration_sec = wav_duration_sec_from_pcm(&mixed);
    let (source_tracks, canonical_timing) = build_combined_gameplay_tracks(&scan)?;
    let source_audio_start_offset_sec = scan.source_midi_offset_sec.unwrap_or(0.0);
    let source_audio_start_offset_pair_count = scan.source_midi_offset_pair_count;
    let audio_start_offset_sec = 0.0;

    // Copy all source MIDI files into features/midi for provenance/debugging.
    let mut tracks_out: Vec<serde_json::Value> = vec![];
    let mut notes_out: Vec<serde_json::Value> = vec![];
    let mut midi_shas: Vec<String> = vec![];
    let mut midi_reference_paths: Vec<String> = vec![];
    let mut copied_names: BTreeMap<String, usize> = BTreeMap::new();

    for m in &scan.midi_files {
        let bytes = fs::read(m).map_err(|e| format!("read midi {}: {e}", m.display()))?;
        midi_shas.push(sha256_hex(&bytes));

        let base_name = m.file_stem().and_then(|s| s.to_str()).unwrap_or("midi");
        let base_id = sanitize_id(base_name);
        let base_id = if base_id.is_empty() {
            "midi".to_string()
        } else {
            base_id
        };
        let next_idx = copied_names.entry(base_id.clone()).or_insert(0);
        *next_idx += 1;
        let copy_id = if *next_idx == 1 {
            base_id
        } else {
            format!("{}_{}", base_id, *next_idx)
        };

        let dst = out_dir
            .join("features")
            .join("midi")
            .join(format!("{copy_id}.mid"));
        fs::write(&dst, &bytes).map_err(|e| format!("write midi copy {}: {e}", dst.display()))?;
        midi_reference_paths.push(format!("features/midi/{copy_id}.mid"));
    }

    let mut lyrics_included = false;
    let mut midi_chart_included = false;
    let mut midi_timing_trust = "advisory";
    let mut chart_timing_status = "authoring_required";
    let mut timing_authority = "advisory";
    let (beats, tempo, sections) = if let Some(timing) = canonical_timing.as_ref() {
        if source_tracks.is_empty() {
            warnings.push(
                "No mapped gameplay MIDI notes were found in the Suno export; copied the source MIDI for reference only."
                    .to_string(),
            );
            let fallback_timing = default_midi_timing_map();
            let beats = generate_beats_from_timing(duration_sec, &fallback_timing);
            let tempo = generate_tempo_map_from_timing(&fallback_timing);
            let sections = generate_sections_from_beats(duration_sec, &beats, 8);
            warnings.push(
                "Beat and tempo scaffolding uses a neutral fallback grid until you run Perform analysis import or author timing manually."
                    .to_string(),
            );
            (beats, tempo, sections)
        } else {
            let mut normalized_tracks: Vec<GameplayTrackNotes> = vec![];
            for track in &source_tracks {
                let Some(role_offset_sec) = resolve_role_normalization_offset_sec(
                    &scan,
                    &track.role,
                    source_audio_start_offset_sec,
                    source_audio_start_offset_pair_count,
                    &mut warnings,
                )?
                else {
                    continue;
                };

                let mut normalized_track = track.clone();
                normalized_track.notes = shift_timed_notes(&track.notes, role_offset_sec);
                // Preserve Suno source MIDI note identities as authoritative data.
                // We still normalize timing against audio when it is safe, but we do
                // not audio-drive drum pitch canonicalization here because that mutates
                // the provided source MIDI into non-matching gameplay output.
                normalized_tracks.push(normalized_track);
            }
            if normalized_tracks.is_empty() {
                warnings.push(
                    "Source MIDI timing could not be normalized safely; copied the source MIDI for reference only."
                        .to_string(),
                );
                let fallback_timing = default_midi_timing_map();
                let beats = generate_beats_from_timing(duration_sec, &fallback_timing);
                let tempo = generate_tempo_map_from_timing(&fallback_timing);
                let sections = generate_sections_from_beats(duration_sec, &beats, 8);
                warnings.push(
                    "Beat and tempo scaffolding uses a neutral fallback grid until you run Perform analysis import or author timing manually."
                        .to_string(),
                );
                (beats, tempo, sections)
            } else {
                write_combined_gameplay_midi(
                    &out_dir.join("features").join("notes.mid"),
                    &normalized_tracks,
                    timing,
                )?;
                for track in &normalized_tracks {
                    tracks_out.push(serde_json::json!({
                        "track_id": track.track_id,
                        "role": track.role,
                        "name": track.name,
                        "channel": track.channel,
                        "source": "suno_source_midi_normalized",
                    }));
                    notes_out.extend(timed_notes_to_json(
                        &track.notes,
                        &track.track_id,
                        "suno_source_midi_normalized",
                    ));
                }
                midi_chart_included = true;
                midi_timing_trust = "normalized_source";
                chart_timing_status = "normalized_from_source_midi";
                timing_authority = "normalized_source";
                if source_audio_start_offset_pair_count > 0
                    && source_audio_start_offset_sec.abs() >= 1e-6
                {
                    let direction = if source_audio_start_offset_sec > 0.0 {
                        "earlier"
                    } else {
                        "later"
                    };
                    warnings.push(format!(
                    "Applied source MIDI start normalization using {} matched audio/MIDI pair(s); median correction {:.3}s {}.",
                    source_audio_start_offset_pair_count,
                    source_audio_start_offset_sec.abs(),
                    direction,
                ));
                } else if source_audio_start_offset_pair_count > 0 {
                    warnings.push(
                    "Source MIDI and audio stem starts already matched closely; imported source MIDI timing directly."
                        .to_string(),
                );
                } else {
                    warnings.push(
                    "No stable audio/MIDI start offset was detected; imported source MIDI timing directly."
                        .to_string(),
                );
                }
                let beats = generate_beats_from_timing(duration_sec, timing);
                let tempo = generate_tempo_map_from_timing(timing);
                let sections = generate_sections_from_beats(duration_sec, &beats, 8);
                (beats, tempo, sections)
            }
        }
    } else {
        warnings.push(
            "Source MIDI timing could not be resolved; copied the source MIDI for reference only."
                .to_string(),
        );
        let fallback_timing = default_midi_timing_map();
        let beats = generate_beats_from_timing(duration_sec, &fallback_timing);
        let tempo = generate_tempo_map_from_timing(&fallback_timing);
        let sections = generate_sections_from_beats(duration_sec, &beats, 8);
        warnings.push(
            "Beat and tempo scaffolding uses a neutral fallback grid until you run Perform analysis import or author timing manually."
                .to_string(),
        );
        (beats, tempo, sections)
    };

    let events_json = serde_json::json!({
        "events_version": "1.0.0",
        "tracks": tracks_out,
        "notes": notes_out,
    });
    fs::write(
        out_dir.join("features").join("events.json"),
        serde_json::to_string_pretty(&events_json).map_err(|e| format!("events json: {e}"))?,
    )
    .map_err(|e| format!("write events.json: {e}"))?;

    fs::write(
        out_dir.join("features").join("beats.json"),
        serde_json::to_string_pretty(&beats).map_err(|e| format!("beats json: {e}"))?,
    )
    .map_err(|e| format!("write beats.json: {e}"))?;

    fs::write(
        out_dir.join("features").join("tempo_map.json"),
        serde_json::to_string_pretty(&tempo).map_err(|e| format!("tempo json: {e}"))?,
    )
    .map_err(|e| format!("write tempo_map.json: {e}"))?;

    fs::write(
        out_dir.join("features").join("sections.json"),
        serde_json::to_string_pretty(&sections).map_err(|e| format!("sections json: {e}"))?,
    )
    .map_err(|e| format!("write sections.json: {e}"))?;

    // IDs
    let wav_bytes = fs::read(&mix_path).map_err(|e| format!("read mix.wav: {e}"))?;
    let audio_sha256 = sha256_hex(&wav_bytes);
    midi_shas.sort();
    let mut h = Sha256::new();
    h.update(format!("raw|{audio_sha256}|{}", midi_shas.join("|")).as_bytes());
    let song_id = hex::encode(h.finalize())[0..32].to_string();

    let manifest = serde_json::json!({
        "schema_version": "1.0.0",
        "song_id": song_id,
        "title": title,
        "artist": artist,
        "duration_sec": (duration_sec * 1_000_000.0).round() / 1_000_000.0,
        "timing": {
            "timebase": "audio",
            "audio_sample_rate_hz": mixed.sample_rate,
            "audio_start_offset_sec": audio_start_offset_sec,
            "source_audio_start_offset_sec": source_audio_start_offset_sec,
            "source_audio_start_offset_pair_count": source_audio_start_offset_pair_count,
            "midi_timing_trust": midi_timing_trust,
            "chart_timing_status": chart_timing_status,
        },
        "source": {
            "kind": "raw_song_data",
            "folder": root.to_string_lossy(),
            "audio_sha256": audio_sha256,
            "midi_sha256": midi_shas,
            "stems": scan.stem_wavs.iter().map(|p| p.to_string_lossy().to_string()).collect::<Vec<_>>(),
            "midis": scan.midi_files.iter().map(|p| p.to_string_lossy().to_string()).collect::<Vec<_>>(),
            "mix_wav": scan.mix_wav.as_ref().map(|p| p.to_string_lossy().to_string()),
            "parts": {
                "stems": scan.stem_parts.iter().map(|part| serde_json::json!({
                    "path": part.path.to_string_lossy().to_string(),
                    "detected_role": part.detected_role.clone(),
                    "game_role": part.game_role.clone(),
                })).collect::<Vec<_>>(),
                "midis": scan.midi_parts.iter().map(|part| serde_json::json!({
                    "path": part.path.to_string_lossy().to_string(),
                    "detected_role": part.detected_role.clone(),
                    "game_role": part.game_role.clone(),
                })).collect::<Vec<_>>(),
                "lyrics_txt": scan.lyrics_txt.as_ref().map(|p| p.to_string_lossy().to_string()),
                "karaoke_json": scan.karaoke_json.as_ref().map(|p| p.to_string_lossy().to_string()),
                "mapped_game_roles": scan.mapped_game_roles.clone(),
            }
        },
        "assets": {
            "audio": {"mix_path": "audio/mix.wav"},
            "midi": {
                "reference_paths": midi_reference_paths,
                "notes_path": if midi_chart_included { Some("features/notes.mid") } else { None::<&str> },
                "timing_authority": timing_authority,
            }
        }
    });
    fs::write(
        out_dir.join("manifest.json"),
        serde_json::to_string_pretty(&manifest).map_err(|e| format!("manifest json: {e}"))?,
    )
    .map_err(|e| format!("write manifest: {e}"))?;

    if let Some(karaoke_json_path) = scan.karaoke_json.as_ref() {
        match fs::read_to_string(karaoke_json_path) {
            Ok(raw) => {
                if serde_json::from_str::<serde_json::Value>(&raw).is_ok() {
                    fs::write(out_dir.join("features").join("lyrics.json"), raw)
                        .map_err(|e| format!("write lyrics.json: {e}"))?;
                    lyrics_included = true;
                } else {
                    warnings.push(format!(
                        "Ignoring invalid karaoke JSON: {}",
                        karaoke_json_path.display()
                    ));
                }
            }
            Err(e) => warnings.push(format!(
                "Failed to read karaoke JSON {}: {e}",
                karaoke_json_path.display()
            )),
        }
    } else if let Some(lyrics_txt_path) = scan.lyrics_txt.as_ref() {
        match fs::read_to_string(lyrics_txt_path) {
            Ok(text) => {
                let align_wav = scan
                    .vocal_stem
                    .as_ref()
                    .or(scan.mix_wav.as_ref())
                    .or_else(|| scan.stem_wavs.first());

                match align_wav {
                    Some(align_path) => match read_wav_pcm16(align_path).and_then(|wav| {
                        build_aligned_lyrics_json(&text, &wav, "raw_song_vocals_align_v1")
                    }) {
                        Ok(lyrics_json) => {
                            fs::write(
                                out_dir.join("features").join("lyrics.json"),
                                serde_json::to_string_pretty(&lyrics_json)
                                    .map_err(|e| format!("lyrics json: {e}"))?,
                            )
                            .map_err(|e| format!("write lyrics.json: {e}"))?;
                            lyrics_included = true;
                        }
                        Err(e) => warnings.push(format!(
                            "Lyrics alignment failed for {}: {e}",
                            lyrics_txt_path.display()
                        )),
                    },
                    None => warnings.push(
                        "lyrics.txt was found, but no audio source was available for alignment."
                            .to_string(),
                    ),
                }
            }
            Err(e) => warnings.push(format!(
                "Failed to read lyrics.txt {}: {e}",
                lyrics_txt_path.display()
            )),
        }
    }

    Ok(ImportRawSongFolderResult {
        songpack_path: out_dir.to_string_lossy().to_string(),
        stems_count: scan.stem_wavs.len(),
        midi_files_count: scan.midi_files.len(),
        lyrics_included,
        midi_chart_included,
        mapped_game_roles: scan.mapped_game_roles.clone(),
        source_midi_offset_sec: scan.source_midi_offset_sec,
        source_midi_offset_pair_count: scan.source_midi_offset_pair_count,
        warnings,
    })
}
