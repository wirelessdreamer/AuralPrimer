//! Filesystem watcher for the songs folder.
//!
//! Listens for create / modify / remove events under `<portable>/data/songs/`
//! (or whatever override the user has configured) and emits a single
//! `songs_folder_changed` Tauri event to the frontend after a short debounce
//! window. The frontend re-runs `scan_songpacks` in response so users see new
//! SongPacks the moment an external tool (e.g. `aural_ingest import`) drops
//! them in.
//!
//! Design notes:
//!
//! - We watch the **current** songs folder directly (recursively) rather than
//!   the parent + filter. This keeps the watcher focused and means the
//!   debounce thread never sees unrelated noise. When the user changes the
//!   override via `set_songs_folder_override`, we tear down the old watcher
//!   and mount a fresh one on the new path. Re-mount is cheap and simpler to
//!   reason about than dynamic include/exclude filtering on a higher root.
//!
//! - Debouncing collapses bursts of events (a single import can create 5-15
//!   files in quick succession) into one frontend refresh. We use a worker
//!   thread that drains a channel and emits the Tauri event after a quiet
//!   period of `DEBOUNCE_MS`.
//!
//! - We deliberately do **not** try to suppress events from writes performed
//!   by the game itself (e.g. demo songpack creation, raw_song imports). The
//!   resulting `scan_songpacks` refresh is idempotent — the cost is a single
//!   redundant scan, which is cheaper than the bookkeeping a suppression
//!   layer would need.
//!
//! - The worker thread is a plain `std::thread`, not a tokio task — the rest
//!   of `src-tauri` is synchronous and we don't want to pull a tokio runtime
//!   in just for this. The thread exits cleanly when the channel sender is
//!   dropped (which happens when the watcher state is replaced or the app
//!   shuts down).

use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use notify::{
    event::{CreateKind, ModifyKind, RemoveKind},
    EventKind, RecommendedWatcher, RecursiveMode, Watcher,
};
use tauri::{AppHandle, Emitter};

/// Event name emitted to the frontend whenever the songs folder changes.
pub const SONGS_FOLDER_CHANGED_EVENT: &str = "songs_folder_changed";

/// Quiet-period after the last raw event before we emit the coalesced
/// `songs_folder_changed` to the frontend. A single `aural_ingest import` can
/// fire ~10 events back to back; 300 ms keeps the refresh feeling instant
/// without causing a refresh storm.
const DEBOUNCE_MS: u64 = 300;

/// Tauri-managed state holding the active watcher (if any). We wrap in a
/// `Mutex` so `set_songs_folder_override` can swap watchers as the path
/// changes.
#[derive(Default)]
pub struct SongsWatchState {
    inner: Mutex<Option<ActiveWatch>>,
}

/// Bundle of "things keeping the watcher alive". Dropping it tears down both
/// the `notify` watcher and the debounce worker thread.
struct ActiveWatch {
    _watcher: RecommendedWatcher,
    /// Holding the sender keeps the debounce worker alive. When the field is
    /// dropped, the channel closes and the worker exits.
    _tx: Sender<WatchSignal>,
    watched_path: PathBuf,
}

/// Internal channel payload. The worker only cares whether *something*
/// changed, not what specifically — `scan_songpacks` on the frontend is the
/// source of truth and runs unconditionally.
enum WatchSignal {
    /// One or more relevant filesystem events arrived; restart the debounce
    /// timer.
    Bump,
}

/// Mount a watcher on the given songs folder. If a watcher is already mounted
/// on the *same* path, this is a no-op. If it's mounted on a different path,
/// the old watcher is torn down first.
///
/// `app` is used both to keep a clone for the worker thread (so it can emit
/// events) and to fetch the managed `SongsWatchState`.
pub fn ensure_watch(app: &AppHandle, songs_folder: &Path) -> Result<(), String> {
    use tauri::Manager;
    let state = app.state::<SongsWatchState>();
    let mut slot = state.inner.lock().map_err(|e| format!("songs_watch lock: {e}"))?;

    if let Some(existing) = slot.as_ref() {
        if existing.watched_path == songs_folder {
            return Ok(());
        }
    }

    // Drop the previous watcher (if any) *before* we create the new one so
    // OS-level handles on the old path are released first.
    *slot = None;

    let active = spawn_watch(app.clone(), songs_folder)?;
    *slot = Some(active);
    Ok(())
}

fn spawn_watch(app: AppHandle, songs_folder: &Path) -> Result<ActiveWatch, String> {
    // Ensure the directory exists; `notify` will error on a missing path. The
    // songs folder is normally created lazily by `scan_songpacks`, but on
    // first launch the watcher boot can race that.
    if let Err(e) = std::fs::create_dir_all(songs_folder) {
        return Err(format!(
            "create songs folder {}: {e}",
            songs_folder.display()
        ));
    }

    let (signal_tx, signal_rx) = channel::<WatchSignal>();
    let signal_tx_for_watcher = signal_tx.clone();

    // notify-rs callback runs on the watcher's own thread. Keep it tiny:
    // classify the event and forward a single Bump signal. Discard noise
    // events (access-only / metadata-only / Any-other) so the debouncer
    // doesn't fire on plain reads.
    let mut watcher = notify::recommended_watcher(
        move |res: notify::Result<notify::Event>| match res {
            Ok(ev) => {
                if is_interesting(&ev.kind) {
                    let _ = signal_tx_for_watcher.send(WatchSignal::Bump);
                }
            }
            Err(e) => {
                // Best-effort logging. We don't want a single error to kill
                // the watcher loop.
                eprintln!("songs_watch: notify error: {e}");
            }
        },
    )
    .map_err(|e| format!("create watcher: {e}"))?;

    watcher
        .watch(songs_folder, RecursiveMode::Recursive)
        .map_err(|e| format!("watch {}: {e}", songs_folder.display()))?;

    let folder_for_thread = songs_folder.to_path_buf();
    thread::Builder::new()
        .name(format!("songs-watch-{}", folder_for_thread.display()))
        .spawn(move || debounce_loop(app, signal_rx))
        .map_err(|e| format!("spawn debounce thread: {e}"))?;

    Ok(ActiveWatch {
        _watcher: watcher,
        _tx: signal_tx,
        watched_path: songs_folder.to_path_buf(),
    })
}

/// Return `true` if the event represents something the library should re-scan
/// for: created, removed, renamed (= modify::name), or any "data" change to a
/// file (which usually means a write completed). Metadata-only events and
/// pure access events are ignored — they're noise.
fn is_interesting(kind: &EventKind) -> bool {
    match kind {
        EventKind::Create(CreateKind::Any | CreateKind::File | CreateKind::Folder | CreateKind::Other) => true,
        EventKind::Remove(RemoveKind::Any | RemoveKind::File | RemoveKind::Folder | RemoveKind::Other) => true,
        EventKind::Modify(ModifyKind::Name(_)) => true,
        EventKind::Modify(ModifyKind::Data(_)) => true,
        // Treat "Modify(Any)" as interesting on platforms (Windows) where
        // notify can't always disambiguate. False positives here just cost
        // one redundant scan.
        EventKind::Modify(ModifyKind::Any) => true,
        // Skip metadata-only, access events, and the catch-all Any/Other.
        _ => false,
    }
}

/// Debounce loop: collapse bursts of `Bump` signals into one emit per quiet
/// period. Exits cleanly when the sender side is dropped.
fn debounce_loop(app: AppHandle, rx: Receiver<WatchSignal>) {
    let debounce = Duration::from_millis(DEBOUNCE_MS);

    loop {
        // Block until we hear about *something*.
        match rx.recv() {
            Ok(WatchSignal::Bump) => {}
            // Sender dropped → app or watcher tearing down; exit cleanly.
            Err(_) => return,
        }

        // Burst-coalesce: keep draining until we hit DEBOUNCE_MS of silence.
        let mut deadline = Instant::now() + debounce;
        loop {
            let now = Instant::now();
            let timeout = deadline.saturating_duration_since(now);
            match rx.recv_timeout(timeout) {
                Ok(WatchSignal::Bump) => {
                    // Reset deadline; more activity is coming.
                    deadline = Instant::now() + debounce;
                }
                Err(RecvTimeoutError::Timeout) => {
                    // Quiet period reached → fire one emit.
                    let _ = app.emit(SONGS_FOLDER_CHANGED_EVENT, ());
                    break;
                }
                Err(RecvTimeoutError::Disconnected) => return,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc::{channel as std_channel, Sender as StdSender};
    use tempfile::tempdir;

    /// Spawn a watcher that forwards `WatchSignal::Bump` to a test channel
    /// instead of through the Tauri AppHandle, so we can assert on raw
    /// notify-side behavior without needing a Tauri runtime.
    fn spawn_raw_watch(
        path: &Path,
        sink: StdSender<()>,
    ) -> (RecommendedWatcher, std::sync::mpsc::Receiver<WatchSignal>) {
        let (signal_tx, signal_rx) = channel::<WatchSignal>();
        let mut watcher = notify::recommended_watcher(
            move |res: notify::Result<notify::Event>| {
                if let Ok(ev) = res {
                    if is_interesting(&ev.kind) {
                        let _ = signal_tx.send(WatchSignal::Bump);
                        let _ = sink.send(());
                    }
                }
            },
        )
        .expect("create watcher");
        watcher
            .watch(path, RecursiveMode::Recursive)
            .expect("watch path");
        (watcher, signal_rx)
    }

    #[test]
    fn watcher_observes_file_creation() {
        let dir = tempdir().expect("tempdir");
        let (sink_tx, sink_rx) = std_channel::<()>();
        let (_watcher, _rx) = spawn_raw_watch(dir.path(), sink_tx);

        // Drop a file in the watched directory.
        let target = dir.path().join("demo.songpack.placeholder");
        std::fs::write(&target, b"hello").expect("write file");

        // Some backends (FSEvents, ReadDirectoryChangesW) take a tick to
        // bubble events up. 1s is generous and matches the spec.
        let signal = sink_rx
            .recv_timeout(Duration::from_secs(1))
            .expect("expected at least one event within 1s");
        let _ = signal;
    }

    #[test]
    fn watcher_observes_nested_directory_creation() {
        // Simulates a real `.songpack/` drop where a directory + manifest
        // appear together.
        let dir = tempdir().expect("tempdir");
        let (sink_tx, sink_rx) = std_channel::<()>();
        let (_watcher, _rx) = spawn_raw_watch(dir.path(), sink_tx);

        let pack = dir.path().join("Example.songpack");
        std::fs::create_dir(&pack).expect("mkdir songpack");
        std::fs::write(pack.join("manifest.json"), b"{}").expect("write manifest");

        // Drain at least one event.
        sink_rx
            .recv_timeout(Duration::from_secs(1))
            .expect("expected at least one event within 1s");
    }

    #[test]
    fn debounce_loop_exits_when_sender_drops() {
        // Sanity check that the worker thread terminates cleanly when its
        // sender side is dropped (i.e. app shutdown / watcher remount). We
        // can't easily test the AppHandle.emit path without a full Tauri
        // setup, but we *can* assert the loop's exit contract.
        let (tx, rx) = channel::<WatchSignal>();
        let handle = thread::spawn(move || {
            // Mirror the inner shape of debounce_loop's recv blocks without
            // requiring an AppHandle. If the loop bug-loops on Disconnected,
            // the join below will hang and the test will time out.
            loop {
                match rx.recv() {
                    Ok(WatchSignal::Bump) => {}
                    Err(_) => return,
                }
            }
        });
        drop(tx);
        handle.join().expect("worker thread joined cleanly");
    }

    #[test]
    fn is_interesting_filters_noise() {
        use notify::event::{AccessKind, AccessMode, MetadataKind};
        assert!(is_interesting(&EventKind::Create(CreateKind::File)));
        assert!(is_interesting(&EventKind::Create(CreateKind::Folder)));
        assert!(is_interesting(&EventKind::Remove(RemoveKind::File)));
        assert!(is_interesting(&EventKind::Modify(ModifyKind::Data(
            notify::event::DataChange::Any
        ))));
        assert!(!is_interesting(&EventKind::Access(AccessKind::Read)));
        assert!(!is_interesting(&EventKind::Access(AccessKind::Open(
            AccessMode::Read
        ))));
        assert!(!is_interesting(&EventKind::Modify(ModifyKind::Metadata(
            MetadataKind::Permissions
        ))));
    }
}
