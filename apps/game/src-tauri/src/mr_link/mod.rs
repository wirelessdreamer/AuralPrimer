//! Mixed-reality link: serves the Unity MR client over the LAN.
//!
//! The host owns MIDI, song position and audio; the headset renders. See
//! `docs/mr-link-protocol.md` for the wire contract — it is the authority, and
//! this module implements exactly what is written there.

pub mod discovery;
