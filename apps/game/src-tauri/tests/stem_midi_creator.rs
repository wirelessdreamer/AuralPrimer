use auralprimer_game_tauri::{stem_midi, wav_mix};
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
    // header: MThd len=6, fmt=0, ntrks=1, division=480
    let mut bytes: Vec<u8> = vec![];
    bytes.extend_from_slice(b"MThd");
    bytes.extend_from_slice(&6u32.to_be_bytes());
    bytes.extend_from_slice(&0u16.to_be_bytes());
    bytes.extend_from_slice(&1u16.to_be_bytes());
    bytes.extend_from_slice(&480u16.to_be_bytes());

    // Track chunk
    let mut trk: Vec<u8> = vec![];
    // delta=0, NoteOn ch0 key60 vel100
    trk.extend_from_slice(&[0x00, 0x90, 60, 100]);
    // delta=480 as VLQ = 0x83 0x60
    trk.extend_from_slice(&[0x83, 0x60, 0x80, 60, 0]);
    // delta=0, EndOfTrack meta
    trk.extend_from_slice(&[0x00, 0xFF, 0x2F, 0x00]);

    bytes.extend_from_slice(b"MTrk");
    bytes.extend_from_slice(&(trk.len() as u32).to_be_bytes());
    bytes.extend_from_slice(&trk);

    fs::write(path, bytes).unwrap();
}

#[test]
fn stem_midi_creator_writes_songpack_artifacts() {
    let td = tempdir().unwrap();
    let songs_dir = td.path().join("songs");
    fs::create_dir_all(&songs_dir).unwrap();

    let stem1 = td.path().join("stem1.wav");
    let stem2 = td.path().join("stem2.wav");
    write_simple_wav(&stem1, 48000, 440.0, 0.25);
    write_simple_wav(&stem2, 48000, 660.0, 0.25);

    let midi = td.path().join("notes.mid");
    write_minimal_midi(&midi);

    let req = stem_midi::StemMidiCreateRequest {
        title: "Test Song".to_string(),
        artist: "Test Artist".to_string(),
        stem_wav_paths: vec![
            stem1.to_string_lossy().to_string(),
            stem2.to_string_lossy().to_string(),
        ],
        midi_path: midi.to_string_lossy().to_string(),
    };

    let res = stem_midi::create_songpack(req, &songs_dir).unwrap();
    let out = std::path::PathBuf::from(res.songpack_path);
    assert!(out.exists());
    assert!(out.join("manifest.json").is_file());
    assert!(out.join("audio").join("mix.wav").is_file());
    assert!(out.join("features").join("notes.mid").is_file());
    assert!(out.join("features").join("events.json").is_file());
}
