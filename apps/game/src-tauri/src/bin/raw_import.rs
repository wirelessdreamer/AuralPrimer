use auralprimer_game_tauri::raw_song;
use std::path::PathBuf;

fn main() {
    let mut args = std::env::args().skip(1);
    let folder = args.next().unwrap_or_else(|| {
        eprintln!("Usage: raw_import <raw_song_folder> <songs_folder> [title] [artist]");
        std::process::exit(2);
    });
    let songs_folder = args.next().unwrap_or_else(|| {
        eprintln!("Usage: raw_import <raw_song_folder> <songs_folder> [title] [artist]");
        std::process::exit(2);
    });
    let title = args.next();
    let artist = args.next();

    let songs_dir = PathBuf::from(songs_folder);
    if let Err(e) = std::fs::create_dir_all(&songs_dir) {
        eprintln!("failed to create songs folder {}: {e}", songs_dir.display());
        std::process::exit(3);
    }

    let req = raw_song::ImportRawSongFolderRequest {
        folder_path: folder,
        title,
        artist,
    };

    match raw_song::import_raw_song_folder(req, &songs_dir) {
        Ok(res) => {
            println!("{}", serde_json::to_string_pretty(&res).unwrap());
        }
        Err(e) => {
            eprintln!("import failed: {e}");
            std::process::exit(1);
        }
    }
}
