use std::fs;
use std::path::Path;

fn write_minimal_wav(path: &Path, sr: u32, duration_sec: f64) {
    // Write a minimal PCM16 mono WAV with silence.
    // This is just enough for our duration parser.
    let num_samples = (duration_sec * sr as f64).round() as u32;
    let byte_rate = sr * 2;
    let data_bytes = num_samples * 2;

    let mut bytes: Vec<u8> = vec![];
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    bytes.extend_from_slice(b"WAVE");

    // fmt chunk
    bytes.extend_from_slice(b"fmt ");
    bytes.extend_from_slice(&(16u32).to_le_bytes());
    bytes.extend_from_slice(&(1u16).to_le_bytes()); // PCM
    bytes.extend_from_slice(&(1u16).to_le_bytes()); // mono
    bytes.extend_from_slice(&sr.to_le_bytes());
    bytes.extend_from_slice(&byte_rate.to_le_bytes());
    bytes.extend_from_slice(&(2u16).to_le_bytes()); // block align
    bytes.extend_from_slice(&(16u16).to_le_bytes()); // bits per sample

    // data chunk
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_bytes.to_le_bytes());
    bytes.resize((44 + data_bytes) as usize, 0);

    fs::write(path, bytes).unwrap();
}

#[test]
fn ghwt_scan_dlc_finds_fixture() {
    let tmp = tempfile::tempdir().unwrap();
    let data_root = tmp.path().join("DATA");
    let dlc_root = data_root
        .join("MODS")
        .join("Guitar Hero_ World Tour Downloadable Content")
        .join("DLC13");
    fs::create_dir_all(dlc_root.join("Content").join("MUSIC")).unwrap();

    fs::write(
        dlc_root.join("song.ini"),
        r#"[SongInfo]
Checksum=DLC13
Title=That Was Just Your Life
Artist=Metallica
Year=2008
"#,
    )
    .unwrap();

    // preview file must exist; for the test we can use a WAV file with a .fsb.xen extension
    write_minimal_wav(
        &dlc_root
            .join("Content")
            .join("MUSIC")
            .join("DLC13_preview.fsb.xen"),
        48_000,
        1.0,
    );

    let songs = auralprimer_game_tauri::ghwt::scan_dlc(&data_root).unwrap();
    assert_eq!(songs.len(), 1);
    assert_eq!(songs[0].checksum, "DLC13");
    assert_eq!(songs[0].title, "That Was Just Your Life");
}

#[test]
fn ghwt_import_preview_writes_songpack_without_vgmstream_when_fixture_is_wav() {
    let tmp = tempfile::tempdir().unwrap();
    let data_root = tmp.path().join("DATA");
    let dlc_dir = data_root
        .join("MODS")
        .join("Guitar Hero_ World Tour Downloadable Content")
        .join("DLC13");
    fs::create_dir_all(dlc_dir.join("Content").join("MUSIC")).unwrap();

    fs::write(
        dlc_dir.join("song.ini"),
        r#"[SongInfo]
Checksum=DLC13
Title=That Was Just Your Life
Artist=Metallica
Year=2008
"#,
    )
    .unwrap();

    write_minimal_wav(
        &dlc_dir
            .join("Content")
            .join("MUSIC")
            .join("DLC13_preview.fsb.xen"),
        48_000,
        2.0,
    );

    let songs_folder = tmp.path().join("songs");
    fs::create_dir_all(&songs_folder).unwrap();

    let res = auralprimer_game_tauri::ghwt::import_preview_to_songpack_to_folder(
        None,
        &data_root,
        "DLC13",
        &songs_folder,
        None,
    )
    .unwrap();

    let songpack = Path::new(&res.songpack_path);
    assert!(songpack.join("manifest.json").is_file());
    assert!(songpack.join("audio").join("mix.wav").is_file());
}

#[test]
fn ghwt_import_uses_stems_and_mixes_when_present() {
    let tmp = tempfile::tempdir().unwrap();
    let data_root = tmp.path().join("DATA");
    let dlc_dir = data_root
        .join("MODS")
        .join("Guitar Hero_ World Tour Downloadable Content")
        .join("DLC13");
    fs::create_dir_all(dlc_dir.join("Content").join("MUSIC")).unwrap();

    fs::write(
        dlc_dir.join("song.ini"),
        r#"[SongInfo]
Checksum=DLC13
Title=That Was Just Your Life
Artist=Metallica
Year=2008
"#,
    )
    .unwrap();

    // Provide 3 stems (as WAVs with .fsb.xen extension). Use different lengths to ensure we fail if mismatch.
    // For this test, they must match duration.
    write_minimal_wav(
        &dlc_dir
            .join("Content")
            .join("MUSIC")
            .join("DLC13_1.fsb.xen"),
        48_000,
        1.0,
    );
    write_minimal_wav(
        &dlc_dir
            .join("Content")
            .join("MUSIC")
            .join("DLC13_2.fsb.xen"),
        48_000,
        1.0,
    );
    write_minimal_wav(
        &dlc_dir
            .join("Content")
            .join("MUSIC")
            .join("DLC13_3.fsb.xen"),
        48_000,
        1.0,
    );

    // Also include a preview, but importer should prefer stems.
    write_minimal_wav(
        &dlc_dir
            .join("Content")
            .join("MUSIC")
            .join("DLC13_preview.fsb.xen"),
        48_000,
        2.0,
    );

    let songs_folder = tmp.path().join("songs");
    fs::create_dir_all(&songs_folder).unwrap();

    let res = auralprimer_game_tauri::ghwt::import_preview_to_songpack_to_folder(
        None,
        &data_root,
        "DLC13",
        &songs_folder,
        None,
    )
    .unwrap();

    assert_eq!(res.used, "stems");
    let songpack = Path::new(&res.songpack_path);
    assert!(songpack.join("manifest.json").is_file());
    assert!(songpack.join("audio").join("mix.wav").is_file());
}

#[test]
fn ghwt_bulk_import_imports_all_scanned_songs() {
    let tmp = tempfile::tempdir().unwrap();
    let data_root = tmp.path().join("DATA");

    // DLC13 + DLC14
    for (dlc, title) in [("DLC13", "One"), ("DLC14", "Two")] {
        let dlc_dir = data_root
            .join("MODS")
            .join("Guitar Hero_ World Tour Downloadable Content")
            .join(dlc);
        fs::create_dir_all(dlc_dir.join("Content").join("MUSIC")).unwrap();
        fs::write(
            dlc_dir.join("song.ini"),
            format!("[SongInfo]\nChecksum={dlc}\nTitle={title}\nArtist=Test\nYear=2008\n"),
        )
        .unwrap();
        write_minimal_wav(
            &dlc_dir
                .join("Content")
                .join("MUSIC")
                .join(format!("{dlc}_preview.fsb.xen")),
            48_000,
            0.25,
        );
    }

    let songs_folder = tmp.path().join("songs");
    fs::create_dir_all(&songs_folder).unwrap();

    let res = auralprimer_game_tauri::ghwt::import_all_to_folder(
        None,
        &data_root,
        &songs_folder,
        None,
    )
    .unwrap();
    assert_eq!(res.len(), 2);
    assert!(res.iter().all(|r| r.ok));
    assert!(songs_folder.join("ghwt_DLC13.songpack").is_dir());
    assert!(songs_folder.join("ghwt_DLC14.songpack").is_dir());
}
