//! Audio decoding helpers (Phase 1.5)
//!
//! Host playback policy: decode common SongPack formats in-process with
//! Symphonia. FFmpeg remains an ingest-sidecar dependency, not a playback
//! dependency.

use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::DecoderOptions;
use symphonia::core::errors::Error;
use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;

use std::io::Cursor;

#[derive(Debug, Clone)]
pub struct DecodedPcm16 {
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub data: Vec<i16>, // interleaved
}

pub const HOST_AUDIO_CODEC_POLICY_ID: &str = "rust_symphonia_in_process";
pub const HOST_AUDIO_CODEC_POLICY: &str =
    "Host playback decodes SongPack audio in-process with Rust/Symphonia; FFmpeg is sidecar-only for ingest conversion.";
pub const HOST_AUDIO_SUPPORTED_MIME_HINTS: &[&str] = &[
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "application/ogg",
    "audio/wav",
    "audio/wave",
    "audio/x-wav",
];

pub fn host_audio_supported_mime_hints() -> &'static [&'static str] {
    HOST_AUDIO_SUPPORTED_MIME_HINTS
}

fn extension_hint_for_mime(mime: &str) -> Option<&'static str> {
    match mime {
        "audio/mpeg" | "audio/mp3" => Some("mp3"),
        "audio/ogg" | "application/ogg" => Some("ogg"),
        "audio/wav" | "audio/wave" | "audio/x-wav" => Some("wav"),
        _ => None,
    }
}

pub fn decode_to_pcm16(bytes: &[u8], mime: &str) -> Result<DecodedPcm16, String> {
    let mut hint = Hint::new();
    if let Some(extension) = extension_hint_for_mime(mime) {
        hint.with_extension(extension);
    }

    let mss = MediaSourceStream::new(Box::new(Cursor::new(bytes.to_vec())), Default::default());

    let probed = symphonia::default::get_probe()
        .format(
            &hint,
            mss,
            &FormatOptions::default(),
            &MetadataOptions::default(),
        )
        .map_err(|e| format!("decode probe error: {e}"))?;

    let mut format = probed.format;

    let track = format
        .default_track()
        .ok_or_else(|| "decode: no default track".to_string())?;

    let codec_params = &track.codec_params;
    let sample_rate_hz = codec_params
        .sample_rate
        .ok_or_else(|| "decode: missing sample_rate".to_string())?;

    let channels = codec_params
        .channels
        .ok_or_else(|| "decode: missing channels".to_string())?
        .count() as u16;

    let mut decoder = symphonia::default::get_codecs()
        .make(codec_params, &DecoderOptions::default())
        .map_err(|e| format!("decode: make decoder: {e}"))?;

    let mut pcm: Vec<i16> = Vec::new();

    loop {
        let packet = match format.next_packet() {
            Ok(p) => p,
            Err(Error::IoError(_)) => break, // EOF
            Err(Error::ResetRequired) => {
                return Err("decode: stream reset required (not supported)".to_string());
            }
            Err(e) => return Err(format!("decode: next_packet: {e}")),
        };

        // Decode the packet into audio samples.
        let decoded = match decoder.decode(&packet) {
            Ok(d) => d,
            Err(Error::IoError(_)) => break,
            Err(Error::DecodeError(_)) => continue, // corrupt packet; try to continue
            Err(e) => return Err(format!("decode: decode: {e}")),
        };

        // IMPORTANT:
        // Symphonia packet sizes can vary during decode. If we allocate a single
        // SampleBuffer based on the first packet and reuse it, later packets can
        // exceed that capacity and trigger an internal panic while copying.
        //
        // For now (Phase 1.5), prefer correctness + stability over micro-allocs.
        let spec = *decoded.spec();
        let duration = decoded.capacity() as u64;
        let mut sbuf = SampleBuffer::<i16>::new(duration, spec);
        sbuf.copy_interleaved_ref(decoded);
        pcm.extend_from_slice(sbuf.samples());
    }

    Ok(DecodedPcm16 {
        sample_rate_hz,
        channels,
        data: pcm,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wav_mix::{write_wav_pcm16, WavPcm16};
    use std::fs;

    #[test]
    fn decode_rejects_invalid_bytes() {
        let err = decode_to_pcm16(b"not audio", "audio/wav").unwrap_err();
        assert!(err.contains("decode probe error"));
    }

    #[test]
    fn codec_policy_is_in_process_symphonia() {
        assert_eq!(HOST_AUDIO_CODEC_POLICY_ID, "rust_symphonia_in_process");
        assert!(HOST_AUDIO_CODEC_POLICY.contains("Symphonia"));
        assert!(HOST_AUDIO_CODEC_POLICY.contains("FFmpeg is sidecar-only"));
        assert!(host_audio_supported_mime_hints().contains(&"audio/ogg"));
        assert_eq!(extension_hint_for_mime("audio/mpeg"), Some("mp3"));
        assert_eq!(extension_hint_for_mime("audio/x-wav"), Some("wav"));
        assert_eq!(extension_hint_for_mime("application/octet-stream"), None);
    }

    #[test]
    fn decode_wav_pcm16_bytes() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join("x.wav");
        let wav = WavPcm16 {
            sample_rate: 48_000,
            channels: 1,
            data: vec![0, 10, -10, 1200, -1200],
        };
        write_wav_pcm16(&p, &wav).unwrap();
        let bytes = fs::read(&p).unwrap();

        let dec = decode_to_pcm16(&bytes, "audio/wav").unwrap();
        assert_eq!(dec.sample_rate_hz, 48_000);
        assert_eq!(dec.channels, 1);
        assert!(!dec.data.is_empty());
    }
}
