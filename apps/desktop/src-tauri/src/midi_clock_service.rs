use crate::midi_clock::{seconds_to_spp_16th, MidiClock};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

#[derive(Debug, Clone)]
pub enum MidiClockCommand {
    /// Open (or re-open) a MIDI output connection using the given port id.
    SelectPort { port_id: usize },
    /// Set tempo for scheduling (quarter notes per minute).
    SetBpm { bpm: f64 },
    /// Seek to an absolute playback time (seconds). Sends SPP best-effort.
    Seek { t_sec: f64 },
    /// Start clock; if already running, no-op.
    Start,
    /// Continue clock; if already running, no-op.
    ///
    /// Use this to resume after a Stop without resetting the song position.
    Continue,
    /// Stop clock; if already stopped, no-op.
    Stop,
    /// Shut down the thread.
    Shutdown,
}

/// Background MIDI clock sender.
///
/// Guarantees:
/// - Best-effort timing; not hard realtime.
/// - Sends SPP on Seek and also right before Start.
/// - Sends Start when transitioning stopped->running.
pub struct MidiClockService {
    tx: Sender<MidiClockCommand>,
    handle: Option<JoinHandle<()>>,
}

impl MidiClockService {
    pub fn spawn() -> Self {
        let (tx, rx) = mpsc::channel::<MidiClockCommand>();
        let handle = thread::spawn(move || run_loop(rx));
        Self {
            tx,
            handle: Some(handle),
        }
    }

    pub fn send(&self, cmd: MidiClockCommand) {
        // best-effort; if receiver died, ignore.
        let _ = self.tx.send(cmd);
    }

    /// Gracefully stop the thread and wait for it.
    pub fn shutdown(mut self) {
        let _ = self.tx.send(MidiClockCommand::Shutdown);
        if let Some(h) = self.handle.take() {
            let _ = h.join();
        }
    }
}

fn run_loop(rx: Receiver<MidiClockCommand>) {
    let mut clock: Option<MidiClock> = None;
    let mut bpm: f64 = 120.0;
    let mut t_sec: f64 = 0.0;
    let mut running = false;

    // scheduling
    let mut next_tick = Instant::now();

    loop {
        // process queued commands
        while let Ok(cmd) = rx.try_recv() {
            match cmd {
                MidiClockCommand::SelectPort { port_id } => {
                    clock = MidiClock::open(port_id).ok();
                }
                MidiClockCommand::SetBpm { bpm: b } => {
                    if b.is_finite() && b > 0.0 {
                        bpm = b;
                    }
                }
                MidiClockCommand::Seek { t_sec: t } => {
                    if t.is_finite() && t >= 0.0 {
                        t_sec = t;
                    }
                    if let Some(c) = clock.as_mut() {
                        let spp = seconds_to_spp_16th(t_sec, bpm);
                        let _ = c.send_song_position_pointer(spp);
                    }
                }
                MidiClockCommand::Start => {
                    if !running {
                        if let Some(c) = clock.as_mut() {
                            // SPP first (best-effort)
                            let spp = seconds_to_spp_16th(t_sec, bpm);
                            let _ = c.send_song_position_pointer(spp);
                            let _ = c.send_start();
                        }
                        running = true;
                        next_tick = Instant::now();
                    }
                }
                MidiClockCommand::Continue => {
                    if !running {
                        if let Some(c) = clock.as_mut() {
                            let _ = c.send_continue();
                        }
                        running = true;
                        next_tick = Instant::now();
                    }
                }
                MidiClockCommand::Stop => {
                    if running {
                        if let Some(c) = clock.as_mut() {
                            let _ = c.send_stop();
                        }
                        running = false;
                    }
                }
                MidiClockCommand::Shutdown => return,
            }
        }

        if !running {
            // Sleep a bit while idle.
            thread::sleep(Duration::from_millis(10));
            continue;
        }

        // tick at 24 PPQN
        let period_sec = 60.0 / (bpm * 24.0);
        let period = Duration::from_secs_f64(period_sec.max(1e-4));

        let now = Instant::now();
        if now >= next_tick {
            if let Some(c) = clock.as_mut() {
                let _ = c.send_clock();
            }
            next_tick += period;
        } else {
            // sleep until next tick (cap)
            let wait = (next_tick - now).min(Duration::from_millis(10));
            thread::sleep(wait);
        }
    }
}
