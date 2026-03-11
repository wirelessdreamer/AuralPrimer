//! Audio decoding helpers (Phase 1.5)
//!
//! Goal: decode common compressed formats (MP3/OGG/Vorbis) into interleaved PCM16.

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

pub fn decode_to_pcm16(bytes: &[u8], mime: &str) -> Result<DecodedPcm16, String> {
    let mut hint = Hint::new();
    // Provide extension hints when possible.
    match mime {
        "audio/mpeg" | "audio/mp3" => {
            hint.with_extension("mp3");
        }
        "audio/ogg" | "application/ogg" => {
            hint.with_extension("ogg");
        }
        "audio/wav" | "audio/wave" | "audio/x-wav" => {
            hint.with_extension("wav");
        }
        _ => {}
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
