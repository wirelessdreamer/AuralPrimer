//! LAN discovery for the mixed-reality client.
//!
//! Multicast beacon + unicast handshake, per `docs/mr-link-protocol.md`. The
//! shape follows a pattern already proven in the author's AugmentedDefense
//! project, including the platform details that decide whether LAN discovery
//! works at all rather than merely appearing to.
//!
//! Two of those are worth stating here because they are invisible until they
//! bite:
//!
//! * The socket binds to a **specific local interface**, not `0.0.0.0`. A
//!   desktop with Ethernet, Wi-Fi and virtual adapters will otherwise beacon
//!   out of whichever the stack picks, which is frequently not the one the
//!   headset is on. The headset side has the mirror-image requirement: Android
//!   must hold a multicast lock or it silently discards multicast entirely.
//! * Every message carries magic and version, and anything else is ignored, so
//!   this cannot be confused by another protocol sharing the group.

use std::io;
use std::net::{IpAddr, Ipv4Addr, SocketAddr, SocketAddrV4, UdpSocket};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

/// IPv4 Local Scope (RFC 2365): meant for local use, not forwarded beyond it.
pub const MULTICAST_GROUP: Ipv4Addr = Ipv4Addr::new(239, 255, 61, 88);
pub const MULTICAST_PORT: u16 = 47761;
pub const BEACON_INTERVAL: Duration = Duration::from_secs(1);

const MAGIC: &str = "AURALPRIMER";
const PROTOCOL_VERSION: u32 = 1;

/// A parsed discovery datagram. Anything not matching the magic and version
/// parses to `None` rather than an error: foreign traffic on a shared multicast
/// group is expected, not exceptional.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DiscoveryMessage {
    Beacon {
        host_ip: String,
        session_port: u16,
        host_name: String,
    },
    Connect {
        client_name: String,
    },
    Ack {
        session_port: u16,
    },
}

impl DiscoveryMessage {
    pub fn encode(&self) -> String {
        match self {
            Self::Beacon {
                host_ip,
                session_port,
                host_name,
            } => format!("{MAGIC}|{PROTOCOL_VERSION}|BEACON|{host_ip}|{session_port}|{host_name}"),
            Self::Connect { client_name } => {
                format!("{MAGIC}|{PROTOCOL_VERSION}|CONNECT|{client_name}")
            }
            Self::Ack { session_port } => {
                format!("{MAGIC}|{PROTOCOL_VERSION}|ACK|{session_port}")
            }
        }
    }

    /// Parse a datagram. Returns `None` for anything that is not ours — wrong
    /// magic, wrong version, malformed — so callers can simply ignore it.
    pub fn parse(raw: &str) -> Option<Self> {
        let mut parts = raw.trim_end_matches(['\r', '\n']).split('|');
        if parts.next()? != MAGIC {
            return None;
        }
        if parts.next()?.parse::<u32>().ok()? != PROTOCOL_VERSION {
            return None;
        }
        match parts.next()? {
            "BEACON" => {
                let host_ip = parts.next()?.to_string();
                let session_port = parts.next()?.parse().ok()?;
                // Host names may legitimately be empty; absent is not the same
                // as empty, so a missing field is still a malformed beacon.
                let host_name = parts.next()?.to_string();
                Some(Self::Beacon {
                    host_ip,
                    session_port,
                    host_name,
                })
            }
            "CONNECT" => Some(Self::Connect {
                client_name: parts.next()?.to_string(),
            }),
            "ACK" => Some(Self::Ack {
                session_port: parts.next()?.parse().ok()?,
            }),
            _ => None,
        }
    }
}

/// The local address the OS would use to reach the network.
///
/// Deliberately does this by `connect`ing a UDP socket to an off-link address
/// and reading back the local end. No packet is sent — UDP connect only fixes
/// the peer locally — but it makes the routing table choose the interface,
/// which is more reliable than enumerating adapters and guessing which of
/// Ethernet, Wi-Fi and a stack of virtual ones is the real path.
pub fn primary_local_ipv4() -> io::Result<Ipv4Addr> {
    let probe = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0))?;
    probe.connect((Ipv4Addr::new(203, 0, 113, 1), 9))?; // TEST-NET-3, never routed
    match probe.local_addr()?.ip() {
        IpAddr::V4(ip) => Ok(ip),
        IpAddr::V6(_) => Err(io::Error::new(
            io::ErrorKind::AddrNotAvailable,
            "no IPv4 address on the outbound interface",
        )),
    }
}

/// Bind the discovery socket to `interface_ip` and join the group on it.
///
/// `SO_REUSEADDR` is set so the port can be shared — necessary on Windows for a
/// second process (a probe, or the headset running on the same machine during
/// development) to also receive the group.
fn bind_discovery_socket(interface_ip: Ipv4Addr) -> io::Result<UdpSocket> {
    use socket2::{Domain, Protocol, Socket, Type};

    let socket = Socket::new(Domain::IPV4, Type::DGRAM, Some(Protocol::UDP))?;
    socket.set_reuse_address(true)?;
    socket.bind(&SocketAddr::from((interface_ip, MULTICAST_PORT)).into())?;
    socket.join_multicast_v4(&MULTICAST_GROUP, &interface_ip)?;
    // Pin outgoing multicast to the same interface we bound, so the beacon
    // cannot leave via a different adapter than the one we advertised.
    socket.set_multicast_if_v4(&interface_ip)?;
    socket.set_multicast_loop_v4(true)?;
    socket.set_read_timeout(Some(Duration::from_millis(200)))?;
    Ok(socket.into())
}

/// Handle to a running beacon. Dropping it stops the thread.
pub struct DiscoveryServer {
    running: Arc<AtomicBool>,
    interface_ip: Ipv4Addr,
}

impl DiscoveryServer {
    /// Start beaconing `session_port` and answering `CONNECT` requests.
    pub fn start(session_port: u16, host_name: String) -> io::Result<Self> {
        let interface_ip = primary_local_ipv4()?;
        let socket = bind_discovery_socket(interface_ip)?;
        let running = Arc::new(AtomicBool::new(true));

        let thread_running = Arc::clone(&running);
        std::thread::Builder::new()
            .name("mr-discovery".into())
            .spawn(move || {
                serve(socket, interface_ip, session_port, host_name, thread_running);
            })?;

        Ok(Self {
            running,
            interface_ip,
        })
    }

    pub fn interface_ip(&self) -> Ipv4Addr {
        self.interface_ip
    }

    pub fn stop(&self) {
        self.running.store(false, Ordering::Relaxed);
    }
}

impl Drop for DiscoveryServer {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Beacon on a timer while answering requests, in one loop.
///
/// The read timeout is what lets a single thread do both without either
/// starving the other or spinning: it wakes often enough to keep the beacon on
/// schedule, and blocks the rest of the time.
fn serve(
    socket: UdpSocket,
    interface_ip: Ipv4Addr,
    session_port: u16,
    host_name: String,
    running: Arc<AtomicBool>,
) {
    let group = SocketAddrV4::new(MULTICAST_GROUP, MULTICAST_PORT);
    let beacon = DiscoveryMessage::Beacon {
        host_ip: interface_ip.to_string(),
        session_port,
        host_name,
    }
    .encode();

    let mut next_beacon = std::time::Instant::now();
    let mut buf = [0u8; 1024];

    while running.load(Ordering::Relaxed) {
        let now = std::time::Instant::now();
        if now >= next_beacon {
            if let Err(e) = socket.send_to(beacon.as_bytes(), group) {
                eprintln!("mr-link: beacon send failed: {e}");
            }
            next_beacon = now + BEACON_INTERVAL;
        }

        match socket.recv_from(&mut buf) {
            Ok((len, from)) => {
                let Ok(text) = std::str::from_utf8(&buf[..len]) else {
                    continue; // not ours; foreign traffic on the group is normal
                };
                if let Some(DiscoveryMessage::Connect { client_name }) =
                    DiscoveryMessage::parse(text)
                {
                    let ack = DiscoveryMessage::Ack { session_port }.encode();
                    match socket.send_to(ack.as_bytes(), from) {
                        Ok(_) => println!("mr-link: {client_name} at {from} -> session {session_port}"),
                        Err(e) => eprintln!("mr-link: ack to {from} failed: {e}"),
                    }
                }
            }
            Err(e)
                if e.kind() == io::ErrorKind::WouldBlock
                    || e.kind() == io::ErrorKind::TimedOut => {}
            Err(e) => eprintln!("mr-link: discovery recv failed: {e}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn beacon_round_trips() {
        let msg = DiscoveryMessage::Beacon {
            host_ip: "192.168.1.20".into(),
            session_port: 47762,
            host_name: "STUDIO-PC".into(),
        };
        assert_eq!(DiscoveryMessage::parse(&msg.encode()), Some(msg));
    }

    #[test]
    fn connect_and_ack_round_trip() {
        for msg in [
            DiscoveryMessage::Connect {
                client_name: "Quest 3".into(),
            },
            DiscoveryMessage::Ack {
                session_port: 47762,
            },
        ] {
            assert_eq!(DiscoveryMessage::parse(&msg.encode()), Some(msg));
        }
    }

    #[test]
    fn beacon_wire_format_is_exactly_as_specified() {
        // Pinned against docs/mr-link-protocol.md: the C# client parses this by
        // hand, so a silent reshaping here would break it with no compile error
        // anywhere.
        let encoded = DiscoveryMessage::Beacon {
            host_ip: "10.0.0.5".into(),
            session_port: 47762,
            host_name: "PC".into(),
        }
        .encode();
        assert_eq!(encoded, "AURALPRIMER|1|BEACON|10.0.0.5|47762|PC");
    }

    #[test]
    fn ignores_foreign_traffic_on_the_group() {
        // AugmentedDefense's beacon shares the mechanism but not our socket;
        // if it ever did, it must not be mistaken for ours.
        assert_eq!(DiscoveryMessage::parse("GameServer|192.168.1.5|47777"), None);
        assert_eq!(DiscoveryMessage::parse("ConnectRequest"), None);
        assert_eq!(DiscoveryMessage::parse(""), None);
        assert_eq!(DiscoveryMessage::parse("random chatter"), None);
    }

    #[test]
    fn rejects_a_different_protocol_version() {
        // Better to ignore a future version than to half-parse it.
        assert_eq!(
            DiscoveryMessage::parse("AURALPRIMER|2|BEACON|10.0.0.5|47762|PC"),
            None
        );
    }

    #[test]
    fn rejects_truncated_messages() {
        assert_eq!(DiscoveryMessage::parse("AURALPRIMER|1|BEACON|10.0.0.5"), None);
        assert_eq!(DiscoveryMessage::parse("AURALPRIMER|1"), None);
        assert_eq!(DiscoveryMessage::parse("AURALPRIMER"), None);
    }

    #[test]
    fn rejects_a_non_numeric_port() {
        assert_eq!(
            DiscoveryMessage::parse("AURALPRIMER|1|BEACON|10.0.0.5|nope|PC"),
            None
        );
    }

    #[test]
    fn tolerates_a_trailing_newline() {
        // Nothing in the spec sends one, but a hand-written test client or a
        // logging relay easily might.
        assert!(matches!(
            DiscoveryMessage::parse("AURALPRIMER|1|ACK|47762\n"),
            Some(DiscoveryMessage::Ack { session_port: 47762 })
        ));
    }

    #[test]
    fn empty_host_name_is_allowed_but_a_missing_field_is_not() {
        assert!(DiscoveryMessage::parse("AURALPRIMER|1|BEACON|10.0.0.5|47762|").is_some());
        assert!(DiscoveryMessage::parse("AURALPRIMER|1|BEACON|10.0.0.5|47762").is_none());
    }

    /// End-to-end over real sockets: beacon out, CONNECT in, ACK back.
    ///
    /// The grammar tests above prove the strings; this proves the part that
    /// actually breaks in the field -- binding, joining the group, and the
    /// interface choice. Skips rather than fails when there is no network, so
    /// it does not turn a laptop on a plane into a red build.
    #[test]
    fn discovers_over_real_sockets() {
        use socket2::{Domain, Protocol, Socket, Type};

        let Ok(interface_ip) = primary_local_ipv4() else {
            eprintln!("no network; skipping live discovery test");
            return;
        };

        let server = match DiscoveryServer::start(47762, "TEST-HOST".into()) {
            Ok(s) => s,
            Err(e) => {
                eprintln!("could not bind discovery socket ({e}); skipping");
                return;
            }
        };

        // Beacon listener: shares the group port with the server.
        let listener = Socket::new(Domain::IPV4, Type::DGRAM, Some(Protocol::UDP)).unwrap();
        listener.set_reuse_address(true).unwrap();
        listener
            .bind(&SocketAddr::from((Ipv4Addr::UNSPECIFIED, MULTICAST_PORT)).into())
            .unwrap();
        listener
            .join_multicast_v4(&MULTICAST_GROUP, &interface_ip)
            .unwrap();
        listener
            .set_read_timeout(Some(Duration::from_millis(500)))
            .unwrap();
        let client: UdpSocket = listener.into();

        // The unicast leg uses a SEPARATE ephemeral socket, which is what the
        // real client must do too. With two sockets sharing port 47761 on one
        // machine, a unicast to that port is delivered to the more specifically
        // bound one -- the server's own socket -- and the ack is swallowed. That
        // is not hypothetical: it is exactly the shape of running the Unity
        // Editor on the host PC during development.
        let unicast = UdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0)).unwrap();
        unicast
            .set_read_timeout(Some(Duration::from_millis(500)))
            .unwrap();

        // 1. Hear a beacon (allow a few intervals for the first one).
        let deadline = std::time::Instant::now() + Duration::from_secs(6);
        let mut buf = [0u8; 1024];
        let mut session_port = None;
        let mut host_addr = None;
        while std::time::Instant::now() < deadline && session_port.is_none() {
            if let Ok((len, from)) = client.recv_from(&mut buf) {
                if let Ok(text) = std::str::from_utf8(&buf[..len]) {
                    if let Some(DiscoveryMessage::Beacon {
                        session_port: port,
                        host_name,
                        ..
                    }) = DiscoveryMessage::parse(text)
                    {
                        if host_name == "TEST-HOST" {
                            session_port = Some(port);
                            host_addr = Some(from);
                        }
                    }
                }
            }
        }
        let session_port = session_port.expect("no beacon heard within 6s");
        assert_eq!(session_port, 47762);

        // 2. Ask to connect, and expect the ack to name the same port.
        let host_addr = host_addr.unwrap();
        let connect = DiscoveryMessage::Connect {
            client_name: "TEST-CLIENT".into(),
        }
        .encode();
        unicast.send_to(connect.as_bytes(), host_addr).unwrap();

        let deadline = std::time::Instant::now() + Duration::from_secs(4);
        let mut acked = None;
        while std::time::Instant::now() < deadline && acked.is_none() {
            if let Ok((len, _)) = unicast.recv_from(&mut buf) {
                if let Ok(text) = std::str::from_utf8(&buf[..len]) {
                    if let Some(DiscoveryMessage::Ack { session_port }) =
                        DiscoveryMessage::parse(text)
                    {
                        acked = Some(session_port);
                    }
                }
            }
        }
        assert_eq!(acked, Some(47762), "no ACK received within 4s");

        server.stop();
    }

    #[test]
    fn finds_a_usable_local_address() {
        // Skipped rather than failed on a machine with no network: this asserts
        // the routing probe works, not that CI has an interface.
        if let Ok(ip) = primary_local_ipv4() {
            assert!(!ip.is_unspecified(), "probe returned 0.0.0.0");
            assert!(!ip.is_multicast());
        }
    }
}
