//! Session server: serves the chart over TCP and streams position + held notes
//! over UDP, per `docs/mr-link-protocol.md` §2–§3.
//!
//! The host is the authority for time and input; the headset renders. Shared
//! state lives in [`HostState`], which the app writes and the session reads —
//! so the transport, the MIDI tracker and this module stay decoupled and none
//! of them has to know the others exist.

use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream, UdpSocket};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use super::protocol::{
    decode_frame_header, encode_frame, frame, host_clock_us, query_library, LibraryQuery,
    LibrarySong, NoteState, PositionSample, PROTOCOL_VERSION,
};

/// How often position is streamed. Fast enough that the client's clock
/// discipline has plenty to work with, cheap enough to be irrelevant on the LAN.
const POSITION_INTERVAL: Duration = Duration::from_millis(16);
/// Held notes are sent on change, and at least this often regardless — so a
/// dropped change corrects itself rather than persisting.
const NOTES_KEEPALIVE: Duration = Duration::from_millis(250);

/// Reads the song library. Supplied by the app rather than implemented here:
/// the app already scans the songs folder for its own library panel, and a
/// second scanner in this module would be a second opinion about what counts as
/// a song, free to drift from the one the desktop shows.
pub type LibraryProvider = Box<dyn Fn() -> Vec<LibrarySong> + Send + Sync>;

/// Turns recorded speech into text. Also the app's job — it owns the sidecar
/// this shells out to, and the protocol layer should not have to know that
/// transcription is a subprocess at all.
pub type Transcriber = Box<dyn Fn(&[u8]) -> Result<String, String> + Send + Sync>;

/// What the session serves. The app updates these; the session only reads —
/// except for `pending_selection`, the one thing that travels the other way. It
/// is a mailbox rather than a call so the session thread never reaches into the
/// app, which would mean holding an app lock while a socket is mid-write.
#[derive(Default)]
pub struct HostState {
    /// Chart JSON for the current song, already serialised (see protocol §4).
    pub chart_json: Mutex<Option<String>>,
    pub position: Mutex<Option<PositionSample>>,
    pub notes: Mutex<NoteState>,
    /// The host's measured audio latency, in seconds. The headset adds its own
    /// predicted display latency on top; see protocol §5.
    pub audio_offset_sec: Mutex<f64>,
    /// Optional capabilities. Absent means the host does not offer that frame,
    /// which is exactly what the `features` list in WELCOME tells the headset.
    pub library: Mutex<Option<LibraryProvider>>,
    pub transcriber: Mutex<Option<Transcriber>>,
    /// The song the headset last asked for, waiting for the app to pick it up.
    ///
    /// One slot, not a queue: if the user picked twice before the app looked,
    /// the second choice is the one they meant, and loading the discarded first
    /// one on the way past would be a visible wrong answer.
    pub pending_selection: Mutex<Option<String>>,
    /// What the headset says its physical keyboard can play, as the JSON it
    /// sent (protocol §7). Held verbatim rather than parsed here: this layer
    /// only has to carry it to the app, which is what decides anything.
    ///
    /// None means no headset has said -- an older client, or one not yet
    /// calibrated. The app must treat that as "assume everything is
    /// playable", which is the behaviour that existed before this frame.
    pub keyboard_layout: Mutex<Option<String>>,
}

impl HostState {
    pub fn set_chart(&self, json: Option<String>) {
        *self.chart_json.lock().unwrap() = json;
    }

    pub fn set_position(&self, song_time_sec: f64, playing: bool) {
        *self.position.lock().unwrap() = Some(PositionSample {
            song_time_sec,
            host_clock_us: host_clock_us(),
            playing,
        });
    }

    /// Replace the held-note set. Sorted by pitch so the encoded form is stable
    /// and the change check below does not fire on reordering alone.
    pub fn set_notes(&self, mut held: Vec<(u8, u8)>) {
        held.sort_unstable();
        *self.notes.lock().unwrap() = NoteState {
            host_clock_us: host_clock_us(),
            held,
        };
    }

    pub fn set_audio_offset_sec(&self, offset: f64) {
        *self.audio_offset_sec.lock().unwrap() = offset;
    }

    pub fn set_library_provider(&self, provider: Option<LibraryProvider>) {
        *self.library.lock().unwrap() = provider;
    }

    pub fn set_transcriber(&self, transcriber: Option<Transcriber>) {
        *self.transcriber.lock().unwrap() = transcriber;
    }

    pub fn keyboard_layout(&self) -> Option<String> {
        self.keyboard_layout.lock().unwrap().clone()
    }

    /// Take the headset's pending song choice, if any, clearing it.
    pub fn take_pending_selection(&self) -> Option<String> {
        self.pending_selection.lock().unwrap().take()
    }

    /// The optional frames this host actually implements, for WELCOME.
    fn features(&self) -> Vec<&'static str> {
        let mut features = Vec::new();
        if self.library.lock().unwrap().is_some() {
            features.push("library");
        }
        if self.transcriber.lock().unwrap().is_some() {
            features.push("voice");
        }
        features
    }
}

/// A running session server.
pub struct SessionServer {
    running: Arc<AtomicBool>,
    tcp_port: u16,
    udp_port: u16,
}

impl SessionServer {
    /// Bind an ephemeral TCP port and the UDP stream port, and start serving.
    pub fn start(state: Arc<HostState>, host_name: String) -> io::Result<Self> {
        // Port 0 lets the OS pick, and discovery advertises whatever we got —
        // so a fixed port cannot collide with anything else on the machine.
        let listener = TcpListener::bind((Ipv4Addr::UNSPECIFIED, 0))?;
        let tcp_port = listener.local_addr()?.port();

        let udp = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0))?;
        let udp_port = udp.local_addr()?.port();

        let running = Arc::new(AtomicBool::new(true));
        let accept_running = Arc::clone(&running);

        std::thread::Builder::new()
            .name("mr-session".into())
            .spawn(move || {
                accept_loop(listener, udp, state, host_name, accept_running);
            })?;

        Ok(Self {
            running,
            tcp_port,
            udp_port,
        })
    }

    pub fn tcp_port(&self) -> u16 {
        self.tcp_port
    }

    pub fn udp_port(&self) -> u16 {
        self.udp_port
    }

    pub fn stop(&self) {
        self.running.store(false, Ordering::Relaxed);
    }
}

impl Drop for SessionServer {
    fn drop(&mut self) {
        self.stop();
    }
}

fn accept_loop(
    listener: TcpListener,
    udp: UdpSocket,
    state: Arc<HostState>,
    host_name: String,
    running: Arc<AtomicBool>,
) {
    // Non-blocking accept so the loop can notice `running` going false rather
    // than parking forever on a connection that never comes.
    if let Err(e) = listener.set_nonblocking(true) {
        eprintln!("mr-link: session listener nonblocking failed: {e}");
        return;
    }

    while running.load(Ordering::Relaxed) {
        match listener.accept() {
            Ok((stream, peer)) => {
                println!("mr-link: session from {peer}");
                let state = Arc::clone(&state);
                let running = Arc::clone(&running);
                let host_name = host_name.clone();
                let udp = match udp.try_clone() {
                    Ok(u) => u,
                    Err(e) => {
                        eprintln!("mr-link: udp clone failed: {e}");
                        continue;
                    }
                };
                std::thread::Builder::new()
                    .name("mr-session-client".into())
                    .spawn(move || {
                        if let Err(e) = serve_client(stream, peer, udp, state, host_name, running) {
                            eprintln!("mr-link: session ended: {e}");
                        }
                    })
                    .ok();
            }
            Err(ref e) if e.kind() == io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(e) => {
                eprintln!("mr-link: accept failed: {e}");
                break;
            }
        }
    }
}

/// Read exactly `buf.len()` bytes, or fail. Short reads are normal on TCP and
/// treating one as a complete frame is how a stream silently desynchronises.
fn read_exact_timeout(stream: &mut TcpStream, buf: &mut [u8]) -> io::Result<()> {
    stream.read_exact(buf)
}

fn serve_client(
    mut stream: TcpStream,
    peer: SocketAddr,
    udp: UdpSocket,
    state: Arc<HostState>,
    host_name: String,
    running: Arc<AtomicBool>,
) -> io::Result<()> {
    // Undo the listener's non-blocking mode, which Windows hands down to every
    // socket it accepts.
    //
    // Left non-blocking, a write that cannot be satisfied immediately fails with
    // WouldBlock instead of waiting, and `write_all` returns that as an error.
    // The only write here big enough to overflow a send buffer is the CHART, so
    // the session died within milliseconds of the handshake — but only to a peer
    // whose buffer actually fills, and only once a song was loaded. Over
    // loopback the whole 88 KB is accepted at once and nothing looks wrong,
    // which is exactly why every desktop-side test of this passed.
    //
    // It also silently disables the read timeout below: timeouts do not apply to
    // a socket that never blocks in the first place.
    stream.set_nonblocking(false)?;

    stream.set_nodelay(true)?;
    // A read timeout keeps a silent peer from pinning this thread forever, and
    // gives the loop a chance to notice shutdown.
    stream.set_read_timeout(Some(Duration::from_millis(200)))?;

    // --- handshake ---
    let mut header = [0u8; 5];
    read_exact_timeout(&mut stream, &mut header)?;
    let Some(hello) = decode_frame_header(&header) else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "implausible frame length in HELLO",
        ));
    };
    if hello.frame_type != frame::HELLO {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("expected HELLO, got frame type {:#04x}", hello.frame_type),
        ));
    }
    let mut payload = vec![0u8; hello.len];
    read_exact_timeout(&mut stream, &mut payload)?;

    // Reject a version mismatch outright. A protocol that half-works across
    // versions is harder to diagnose than one that refuses.
    let hello_json = serde_json::from_slice::<serde_json::Value>(&payload).ok();
    let client_protocol = hello_json
        .as_ref()
        .and_then(|v| v.get("protocol").and_then(|p| p.as_u64()))
        .unwrap_or(0);
    // Where the client wants its streams. A client that names a port has already
    // bound it, which is strictly better than the host picking a number and
    // hoping it happens to be free at the other end. Absent means an older
    // client that expects the host's own port number, so keep doing that.
    let client_udp_port = hello_json
        .as_ref()
        .and_then(|v| v.get("udpPort").and_then(|p| p.as_u64()))
        .filter(|p| *p > 0 && *p <= u16::MAX as u64)
        .map(|p| p as u16);
    if client_protocol != PROTOCOL_VERSION as u64 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("client protocol {client_protocol}, host speaks {PROTOCOL_VERSION}"),
        ));
    }

    let welcome = serde_json::json!({
        "host": host_name,
        "protocol": PROTOCOL_VERSION,
        "udpPort": udp.local_addr()?.port(),
        "audioOffsetSec": *state.audio_offset_sec.lock().unwrap(),
        // Which optional frames this host implements. New frame types do not
        // break an old peer -- both ends ignore types they do not know -- but
        // silence is indistinguishable from a dropped request, so a headset
        // that asked an old host for a library would wait forever. See §6.
        "features": state.features(),
    });
    stream.write_all(&encode_frame(
        frame::WELCOME,
        welcome.to_string().as_bytes(),
    ))?;

    // The chart, if a song is loaded. If none is, the headset simply waits for
    // a SONG_CHANGED rather than being told an empty chart is a real one.
    if let Some(chart) = state.chart_json.lock().unwrap().clone() {
        stream.write_all(&encode_frame(frame::CHART, chart.as_bytes()))?;
    }

    // --- streams ---
    // UDP goes to the peer's address, on the port the client nominated in HELLO
    // (falling back to the host's own port for clients that do not name one).
    // The client never sends on UDP, so the address comes from the TCP peer.
    let stream_addr = SocketAddr::new(
        peer.ip(),
        client_udp_port.unwrap_or(udp.local_addr()?.port()),
    );
    let stream_state = Arc::clone(&state);
    let stream_running = Arc::clone(&running);
    let streamer = std::thread::Builder::new()
        .name("mr-session-stream".into())
        .spawn(move || stream_loop(udp, stream_addr, stream_state, stream_running))?;

    // --- request loop ---
    let mut last_chart: Option<String> = state.chart_json.lock().unwrap().clone();
    while running.load(Ordering::Relaxed) {
        // Push a new chart when the song changes underneath us.
        let current = state.chart_json.lock().unwrap().clone();
        if current != last_chart {
            if let Some(chart) = &current {
                stream.write_all(&encode_frame(frame::CHART, chart.as_bytes()))?;
            }
            last_chart = current;
        }

        match stream.read_exact(&mut header) {
            Ok(()) => {}
            Err(ref e)
                if e.kind() == io::ErrorKind::WouldBlock || e.kind() == io::ErrorKind::TimedOut =>
            {
                continue;
            }
            Err(e) => {
                let _ = streamer;
                return Err(e);
            }
        }

        let Some(frame_header) = decode_frame_header(&header) else {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "implausible frame length",
            ));
        };
        let mut payload = vec![0u8; frame_header.len];
        if frame_header.len > 0 {
            read_exact_timeout(&mut stream, &mut payload)?;
        }

        match frame_header.frame_type {
            frame::PING => {
                // Echo the client's stamp and add ours, so the client can solve
                // for offset and RTT from one exchange.
                if payload.len() == 8 {
                    let mut pong = Vec::with_capacity(16);
                    pong.extend_from_slice(&payload);
                    pong.extend_from_slice(&host_clock_us().to_le_bytes());
                    stream.write_all(&encode_frame(frame::PONG, &pong))?;
                }
            }
            frame::TRANSPORT => {
                // Parsed and logged; wiring to the real transport is the next
                // step and deliberately not faked here.
                if let Ok(v) = serde_json::from_slice::<serde_json::Value>(&payload) {
                    println!("mr-link: transport request {v}");
                }
            }
            frame::LIBRARY_REQUEST => {
                // A malformed query is treated as the empty one rather than as
                // an error: the headset asking badly should see the library, not
                // lose its connection.
                let query: LibraryQuery = serde_json::from_slice(&payload).unwrap_or_default();

                // The scan runs under the lock. It is a directory listing, it
                // happens only when someone opens the Songs menu, and the
                // alternative -- cloning the provider out -- is not available
                // for a boxed closure.
                let songs = state.library.lock().unwrap().as_ref().map(|read| read());

                if let Some(songs) = songs {
                    let page = query_library(&songs, &query);
                    match serde_json::to_string(&page) {
                        Ok(json) => {
                            stream.write_all(&encode_frame(frame::LIBRARY, json.as_bytes()))?;
                        }
                        Err(e) => eprintln!("mr-link: cannot serialise library page: {e}"),
                    }
                } else {
                    // Said so in WELCOME already; answering nothing is correct.
                    eprintln!("mr-link: library requested but no provider is set");
                }
            }
            frame::KEYBOARD_LAYOUT => {
                // Kept, not consumed: unlike a song choice this is standing
                // state, and the app reads it every time it rebuilds what it
                // is waiting for.
                match std::str::from_utf8(&payload) {
                    Ok(json) => {
                        println!("mr-link: headset keyboard {json}");
                        *state.keyboard_layout.lock().unwrap() = Some(json.to_string());
                    }
                    Err(e) => eprintln!("mr-link: keyboard layout was not UTF-8: {e}"),
                }
            }
            frame::SELECT_SONG => {
                if let Some(id) = serde_json::from_slice::<serde_json::Value>(&payload)
                    .ok()
                    .and_then(|v| {
                        v.get("songId")
                            .and_then(|s| s.as_str())
                            .map(str::to_string)
                    })
                {
                    // Posted, not acted on. Loading a song is the app's job, and
                    // the existing SONG_CHANGED -> CHART flow is what tells the
                    // headset it worked -- so there is deliberately no reply
                    // here to become a second source of truth.
                    println!("mr-link: headset selected song {id}");
                    *state.pending_selection.lock().unwrap() = Some(id);
                }
            }
            frame::VOICE_QUERY => {
                // Transcription takes a second or so and this blocks the read
                // loop while it runs. That is deliberate: the alternative is a
                // second thread writing frames into the same socket as this one,
                // which interleaves them. The stall is safe because the client
                // picks the LOWEST-RTT sample in its window to set its clock
                // from, so the one delayed PONG is never the sample it uses.
                let transcribed = state
                    .transcriber
                    .lock()
                    .unwrap()
                    .as_ref()
                    .map(|hear| hear(&payload));

                let reply = match transcribed {
                    Some(Ok(text)) => serde_json::json!({ "text": text, "error": null }),
                    Some(Err(e)) => serde_json::json!({ "text": "", "error": e }),
                    None => serde_json::json!({
                        "text": "",
                        "error": "this host has no speech recogniser installed",
                    }),
                };
                stream.write_all(&encode_frame(
                    frame::VOICE_RESULT,
                    reply.to_string().as_bytes(),
                ))?;
            }
            other => {
                eprintln!("mr-link: ignoring unexpected frame type {other:#04x}");
            }
        }
    }

    Ok(())
}

/// Stream position at a fixed rate, and notes on change with a keepalive.
fn stream_loop(
    udp: UdpSocket,
    to: SocketAddr,
    state: Arc<HostState>,
    running: Arc<AtomicBool>,
) {
    let mut next_position = Instant::now();
    let mut next_notes_keepalive = Instant::now();
    let mut last_notes: Option<NoteState> = None;

    while running.load(Ordering::Relaxed) {
        let now = Instant::now();

        if now >= next_position {
            if let Some(sample) = *state.position.lock().unwrap() {
                let _ = udp.send_to(&sample.encode(), to);
            }
            next_position = now + POSITION_INTERVAL;
        }

        let notes = state.notes.lock().unwrap().clone();
        let changed = last_notes.as_ref().map(|n| n.held != notes.held) != Some(false);
        if changed || now >= next_notes_keepalive {
            let _ = udp.send_to(&notes.encode(), to);
            last_notes = Some(notes);
            next_notes_keepalive = now + NOTES_KEEPALIVE;
        }

        std::thread::sleep(Duration::from_millis(4));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn read_frame(stream: &mut TcpStream) -> (u8, Vec<u8>) {
        let mut header = [0u8; 5];
        stream.read_exact(&mut header).expect("frame header");
        let h = decode_frame_header(&header).expect("plausible header");
        let mut payload = vec![0u8; h.len];
        if h.len > 0 {
            stream.read_exact(&mut payload).expect("frame payload");
        }
        (h.frame_type, payload)
    }

    fn connect(server: &SessionServer) -> TcpStream {
        let stream = TcpStream::connect((Ipv4Addr::LOCALHOST, server.tcp_port()))
            .expect("connect to session");
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        stream
    }

    fn hello(protocol: u32) -> Vec<u8> {
        encode_frame(
            frame::HELLO,
            serde_json::json!({ "client": "test", "protocol": protocol })
                .to_string()
                .as_bytes(),
        )
    }

    #[test]
    fn handshake_returns_welcome_with_the_stream_port_and_audio_offset() {
        let state = Arc::new(HostState::default());
        state.set_audio_offset_sec(0.042);
        let server = SessionServer::start(Arc::clone(&state), "TEST".into()).unwrap();

        let mut client = connect(&server);
        client.write_all(&hello(PROTOCOL_VERSION)).unwrap();

        let (frame_type, payload) = read_frame(&mut client);
        assert_eq!(frame_type, frame::WELCOME);
        let v: serde_json::Value = serde_json::from_slice(&payload).unwrap();
        assert_eq!(v["protocol"], 1);
        assert_eq!(v["host"], "TEST");
        assert_eq!(v["udpPort"], server.udp_port());
        // The host's own audio latency travels to the client; its video offset
        // deliberately does not.
        assert!((v["audioOffsetSec"].as_f64().unwrap() - 0.042).abs() < 1e-9);
    }

    /// The client names its own receive port, and the host must actually use
    /// it. If the host keeps streaming to its own port number instead, the
    /// session looks perfectly healthy over TCP while no position or note ever
    /// arrives — the failure this addressing change exists to prevent.
    /// A client that is slow to drain its socket must not be hung up on.
    ///
    /// The CHART is the only frame large enough to overflow a send buffer, so
    /// this only ever bit a real headset over Wi-Fi with a song loaded — never
    /// over loopback, where the whole thing is accepted at once. A non-blocking
    /// socket turns "wait a moment" into an error, and the session died
    /// milliseconds after the handshake.
    /// Accepted sockets inherit the listener's non-blocking mode on Windows.
    ///
    /// This is the hazard `serve_client` undoes with `set_nonblocking(false)`.
    /// Left inherited, a write that cannot complete at once fails with
    /// WouldBlock rather than waiting, and `write_all` surfaces that as an
    /// error — which killed the session the moment the CHART was large enough
    /// to fill a real peer's send buffer. The handshake still succeeded,
    /// because the HELLO was already buffered by the time it was read, so the
    /// failure looked like the host hanging up for no reason.
    ///
    /// Not reproducible over loopback at any practical chart size: the loopback
    /// buffer accepts megabytes without blocking, which is why every
    /// desktop-side test of this passed while a headset on Wi-Fi failed every
    /// time. So assert the platform behaviour that makes the fix necessary,
    /// rather than a symptom this machine cannot produce.
    #[test]
    fn accepted_sockets_inherit_the_listeners_non_blocking_mode() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        listener.set_nonblocking(true).unwrap();

        let _client = TcpStream::connect((Ipv4Addr::LOCALHOST, port)).unwrap();

        let mut accepted = None;
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline && accepted.is_none() {
            match listener.accept() {
                Ok((s, _)) => accepted = Some(s),
                Err(ref e) if e.kind() == io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(10))
                }
                Err(e) => panic!("accept failed: {e}"),
            }
        }
        let mut server = accepted.expect("no connection accepted");

        let mut buf = [0u8; 1];
        let inherited = matches!(
            server.read(&mut buf),
            Err(ref e) if e.kind() == io::ErrorKind::WouldBlock
        );

        // Putting it back to blocking is what makes reads honour the timeout and
        // writes wait instead of failing.
        server.set_nonblocking(false).unwrap();
        server
            .set_read_timeout(Some(Duration::from_millis(50)))
            .unwrap();
        let timed_out = matches!(
            server.read(&mut buf),
            Err(ref e) if e.kind() == io::ErrorKind::WouldBlock || e.kind() == io::ErrorKind::TimedOut
        );

        assert!(
            inherited,
            "accepted socket was already blocking; if this ever holds, revisit              the set_nonblocking(false) in serve_client and the reasoning above"
        );
        assert!(timed_out, "the read timeout did not take effect after clearing non-blocking");
    }

    #[test]
    fn streams_go_to_the_port_the_client_nominated() {
        let state = Arc::new(HostState::default());
        state.set_position(12.5, true);
        let server = SessionServer::start(Arc::clone(&state), "TEST".into()).unwrap();

        // Bind first, exactly as the client does, then advertise that port.
        let sink = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        sink.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
        let sink_port = sink.local_addr().unwrap().port();

        let mut client = connect(&server);
        client
            .write_all(&encode_frame(
                frame::HELLO,
                serde_json::json!({
                    "client": "test",
                    "protocol": PROTOCOL_VERSION,
                    "udpPort": sink_port,
                })
                .to_string()
                .as_bytes(),
            ))
            .unwrap();

        let (frame_type, _) = read_frame(&mut client);
        assert_eq!(frame_type, frame::WELCOME);

        // A port distinct from the host's own proves the nomination was honoured
        // rather than coincidentally matching.
        assert_ne!(sink_port, server.udp_port());

        let mut buf = [0u8; 256];
        let (len, _) = sink.recv_from(&mut buf).expect("no stream on the nominated port");
        assert!(len > 0);
    }

    #[test]
    fn a_loaded_chart_is_pushed_immediately_after_welcome() {
        let state = Arc::new(HostState::default());
        state.set_chart(Some(r#"{"songId":"psalm-6"}"#.into()));
        let server = SessionServer::start(Arc::clone(&state), "TEST".into()).unwrap();

        let mut client = connect(&server);
        client.write_all(&hello(PROTOCOL_VERSION)).unwrap();

        assert_eq!(read_frame(&mut client).0, frame::WELCOME);
        let (frame_type, payload) = read_frame(&mut client);
        assert_eq!(frame_type, frame::CHART);
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&payload).unwrap()["songId"],
            "psalm-6"
        );
    }

    #[test]
    fn no_chart_is_sent_when_no_song_is_loaded() {
        // Better that the headset waits than that it is handed an empty chart
        // and treats it as a real one.
        let state = Arc::new(HostState::default());
        let server = SessionServer::start(Arc::clone(&state), "TEST".into()).unwrap();

        let mut client = connect(&server);
        client.write_all(&hello(PROTOCOL_VERSION)).unwrap();
        assert_eq!(read_frame(&mut client).0, frame::WELCOME);

        client
            .set_read_timeout(Some(Duration::from_millis(300)))
            .unwrap();
        let mut header = [0u8; 5];
        assert!(client.read_exact(&mut header).is_err(), "unexpected frame");
    }

    #[test]
    fn ping_is_answered_with_both_timestamps() {
        let state = Arc::new(HostState::default());
        let server = SessionServer::start(Arc::clone(&state), "TEST".into()).unwrap();

        let mut client = connect(&server);
        client.write_all(&hello(PROTOCOL_VERSION)).unwrap();
        assert_eq!(read_frame(&mut client).0, frame::WELCOME);

        let sent_at: u64 = 1_234_567;
        client
            .write_all(&encode_frame(frame::PING, &sent_at.to_le_bytes()))
            .unwrap();

        let (frame_type, payload) = read_frame(&mut client);
        assert_eq!(frame_type, frame::PONG);
        assert_eq!(payload.len(), 16);
        // The echo must come back bit-identical: the client subtracts it to get
        // RTT, so any mangling silently corrupts the clock offset.
        assert_eq!(u64::from_le_bytes(payload[..8].try_into().unwrap()), sent_at);
        assert!(u64::from_le_bytes(payload[8..].try_into().unwrap()) > 0);
    }

    #[test]
    fn a_protocol_mismatch_is_refused_rather_than_half_served() {
        let state = Arc::new(HostState::default());
        let server = SessionServer::start(Arc::clone(&state), "TEST".into()).unwrap();

        let mut client = connect(&server);
        client.write_all(&hello(99)).unwrap();

        let mut header = [0u8; 5];
        assert!(
            client.read_exact(&mut header).is_err(),
            "host answered a mismatched protocol version"
        );
    }

    #[test]
    fn position_and_notes_stream_over_udp() {
        let state = Arc::new(HostState::default());
        state.set_position(12.5, true);
        state.set_notes(vec![(64, 90), (60, 100)]);
        let server = SessionServer::start(Arc::clone(&state), "TEST".into()).unwrap();

        // Bind the port the host will stream to before completing the
        // handshake, so nothing is missed in the gap.
        let listener = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, server.udp_port()));
        let Ok(listener) = listener else {
            eprintln!("stream port busy; skipping");
            return;
        };
        listener
            .set_read_timeout(Some(Duration::from_millis(500)))
            .unwrap();

        let mut client = connect(&server);
        client.write_all(&hello(PROTOCOL_VERSION)).unwrap();
        assert_eq!(read_frame(&mut client).0, frame::WELCOME);

        let mut saw_position = false;
        let mut saw_notes = false;
        let deadline = Instant::now() + Duration::from_secs(3);
        let mut buf = [0u8; 512];
        while Instant::now() < deadline && !(saw_position && saw_notes) {
            let Ok((len, _)) = listener.recv_from(&mut buf) else {
                continue;
            };
            if let Some(p) = PositionSample::decode(&buf[..len]) {
                assert_eq!(p.song_time_sec, 12.5);
                assert!(p.playing);
                saw_position = true;
            }
            if let Some(n) = NoteState::decode(&buf[..len]) {
                // set_notes sorts, so the chord arrives pitch-ascending.
                assert_eq!(n.held, vec![(60, 100), (64, 90)]);
                saw_notes = true;
            }
        }
        assert!(saw_position, "no POSITION datagram within 3s");
        assert!(saw_notes, "no NOTES datagram within 3s");
    }

    #[test]
    fn held_notes_are_sorted_so_reordering_alone_is_not_a_change() {
        let state = HostState::default();
        state.set_notes(vec![(67, 80), (60, 100), (64, 90)]);
        assert_eq!(
            state.notes.lock().unwrap().held,
            vec![(60, 100), (64, 90), (67, 80)]
        );
    }
}
