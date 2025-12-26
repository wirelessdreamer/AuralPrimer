use auralprimer_desktop_tauri::{raw_song, wav_mix};
use std::fs;
use tempfile::tempdir;

fn write_simple_wav(path: &std::path::Path, sr: u32, hz: f64, sec: f64) {
    let frames = (sr as f64 * sec).round() as usize;
    let mut data: Vec<i16> = Vec::with_capacity(frames);
    for i in 0..frames {
        let t = (i as f64) / (sr as f64);
        let s = (t * hz * std::f64::consts::TAU).sin();
        data.push((s * 5000.0) as i16);
    }
    let wav = wav_mix::WavPcm16 {
        sample_rate: sr,
        channels: 1,
        data,
    };
    wav_mix::write_wav_pcm16(path, &wav).unwrap();
}

fn write_minimal_midi(path: &std::path::Path) {
    // A tiny format-0 SMF: one note on at tick 0, off at tick 480.
    let mut bytes: Vec<u8> = vec![];
    bytes.extend_from_slice(b"MThd");
    bytes.extend_from_slice(&6u32.to_be_bytes());
    bytes.extend_from_slice(&0u16.to_be_bytes());
    bytes.extend_from_slice(&1u16.to_be_bytes());
    bytes.extend_from_slice(&480u16.to_be_bytes());

    let mut trk: Vec<u8> = vec![];
    trk.extend_from_slice(&[0x00, 0x90, 60, 100]);
    trk.extend_from_slice(&[0x83, 0x60, 0x80, 60, 0]);
    trk.extend_from_slice(&[0x00, 0xFF, 0x2F, 0x00]);

    bytes.extend_from_slice(b"MTrk");
    bytes.extend_from_slice(&(trk.len() as u32).to_be_bytes());
    bytes.extend_from_slice(&trk);

    fs::write(path, bytes).unwrap();
}

#[test]
fn raw_song_importer_writes_songpack_artifacts() {
    let td = tempdir().unwrap();
    let songs_dir = td.path().join("songs");
    fs::create_dir_all(&songs_dir).unwrap();

    let raw_dir = td.path().join("RawSong");
    fs::create_dir_all(&raw_dir).unwrap();

    // Two stems with slightly different lengths (padding tolerance)
    write_simple_wav(&raw_dir.join("Song (Guitar).wav"), 48_000, 440.0, 0.25);
    write_simple_wav(&raw_dir.join("Song (Drums).wav"), 48_000, 660.0, 0.26);

    write_minimal_midi(&raw_dir.join("Song (Guitar).mid"));
    write_minimal_midi(&raw_dir.join("Song (Drums).mid"));

    // Optional PsalmsKaraoke lyrics output path
    let out = raw_dir.join("psalms_output");
    fs::create_dir_all(&out).unwrap();
    fs::write(
        out.join("lyrics_karaoke.karaoke.json"),
        r#"{ "format": "psalms_karaoke_json_v1", "granularity": "syllable", "lines": [] }"#,
    )
    .unwrap();

    let res = raw_song::import_raw_song_folder(
        raw_song::ImportRawSongFolderRequest {
            folder_path: raw_dir.to_string_lossy().to_string(),
            title: Some("My Song".to_string()),
            artist: Some("Me".to_string()),
        },
        &songs_dir,
    )
    .unwrap();

    let sp = std::path::PathBuf::from(res.songpack_path);
    assert!(sp.exists());
    assert!(sp.join("manifest.json").is_file());
    assert!(sp.join("audio").join("mix.wav").is_file());
    assert!(sp.join("features").join("notes.mid").is_file());
    assert!(sp.join("features").join("events.json").is_file());
    assert!(sp.join("features").join("beats.json").is_file());
    assert!(sp.join("features").join("tempo_map.json").is_file());
    assert!(sp.join("features").join("sections.json").is_file());
    assert!(sp.join("charts").join("easy.json").is_file());
    assert!(sp.join("features").join("lyrics.json").is_file());

    assert_eq!(res.stems_count, 2);
    assert_eq!(res.midi_files_count, 2);
    assert!(res.lyrics_included);
}

