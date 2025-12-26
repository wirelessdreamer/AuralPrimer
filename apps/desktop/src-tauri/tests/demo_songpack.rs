use auralprimer_desktop_tauri::demo_songpack;
use std::fs;
use tempfile::tempdir;

#[test]
fn demo_songpack_is_created_and_has_audio() {
    let td = tempdir().unwrap();
    let songs_dir = td.path().join("songs");
    fs::create_dir_all(&songs_dir).unwrap();

    let created = demo_songpack::ensure_demo_songpack(&songs_dir).unwrap();
    let out = created.expect("expected demo songpack to be created");

    assert!(out.exists());
    assert!(out.join("manifest.json").is_file());
    assert!(out.join("audio").join("mix.wav").is_file());

    // Calling again should be a no-op.
    let created2 = demo_songpack::ensure_demo_songpack(&songs_dir).unwrap();
    assert!(created2.is_none());
}

