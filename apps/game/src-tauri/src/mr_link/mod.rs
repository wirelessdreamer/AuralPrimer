//! Mixed-reality link: serves the Unity MR client over the LAN.
//!
//! The host owns MIDI, song position and audio; the headset renders. See
//! `docs/mr-link-protocol.md` for the wire contract — it is the authority, and
//! these modules implement exactly what is written there.
//!
//! Layering: [`protocol`] is pure encoding with no I/O so every byte is
//! testable, [`discovery`] is the multicast beacon and handshake, and
//! [`session`] serves the chart and streams position and held notes.

pub mod discovery;
pub mod protocol;
pub mod session;

use std::io;
use std::sync::Arc;

pub use session::HostState;

/// The whole link: discovery advertising a live session.
///
/// Started together because a beacon advertising a port nothing listens on is
/// worse than no beacon at all — the headset would connect, fail, and retry
/// forever with no indication why.
pub struct MrLink {
    _session: session::SessionServer,
    _discovery: discovery::DiscoveryServer,
    host_name: String,
    pub state: Arc<HostState>,
}

impl MrLink {
    pub fn start(host_name: String) -> io::Result<Self> {
        let state = Arc::new(HostState::default());
        let session = session::SessionServer::start(Arc::clone(&state), host_name.clone())?;
        let discovery = discovery::DiscoveryServer::start(session.tcp_port(), host_name.clone())?;
        println!(
            "mr-link: listening on tcp/{} udp/{}, beaconing from {}",
            session.tcp_port(),
            session.udp_port(),
            discovery.interface_ip()
        );
        Ok(Self {
            _session: session,
            _discovery: discovery,
            host_name,
            state,
        })
    }

    pub fn host_name(&self) -> &str {
        &self.host_name
    }
}
