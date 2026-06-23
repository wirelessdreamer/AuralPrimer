//! Shared AuralSong contract logic for the AuralPrimer Tauri shells.
//!
//! Both `apps/game/src-tauri` and `apps/desktop/src-tauri` scan the songs
//! folder for `.auralsong` containers, read `manifest.json`, and surface a small
//! summary to the frontend. That logic used to live as a near-verbatim copy in
//! each `lib.rs`; it now lives here once.
//!
//! Modules:
//! - [`manifest`] — manifest parsing (with UTF-8 BOM strip), the dir/zip
//!   readers, and the [`ManifestSummary`] / [`AuralSongScanEntry`] summary types.
//! - [`songs_watch`] — filesystem watcher that emits a debounced
//!   `songs_folder_changed` Tauri event when the songs folder changes.

pub mod manifest;
pub mod songs_watch;

pub use manifest::{
    parse_manifest_json, parse_manifest_raw, read_dir_manifest, read_dir_manifest_raw,
    read_zip_manifest, read_zip_manifest_raw, AuralSongScanEntry, ManifestSummary,
};
pub use songs_watch::{ensure_watch, SongsWatchState, SONGS_FOLDER_CHANGED_EVENT};
