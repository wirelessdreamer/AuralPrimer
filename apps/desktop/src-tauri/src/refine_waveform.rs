use crate::audio_decode::DecodedPcm16;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct RefineWaveform {
    pub duration_sec: f64,
    pub peaks: Vec<f32>,
}

/// A bounded peak envelope of the actual source, retaining transients in
/// either stereo channel rather than canceling opposite-phase samples.
pub fn summarize(audio: &DecodedPcm16) -> Result<RefineWaveform, String> {
    let channels = audio.channels as usize;
    if channels == 0 || audio.sample_rate_hz == 0 || !audio.data.len().is_multiple_of(channels) {
        return Err("invalid source audio frames".into());
    }
    let frames = audio.data.len() / channels;
    let count = frames.min(16_384);
    let mut peaks = vec![0.0f32; count];
    for (frame, samples) in audio.data.chunks_exact(channels).enumerate() {
        let bucket = frame * count / frames;
        for sample in samples {
            peaks[bucket] = peaks[bucket].max((*sample as f32).abs() / 32768.0);
        }
    }
    Ok(RefineWaveform { duration_sec: frames as f64 / audio.sample_rate_hz as f64, peaks })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retains_stereo_transients_without_phase_cancellation() {
        let waveform = summarize(&DecodedPcm16 { sample_rate_hz: 2, channels: 2, data: vec![0, 0, i16::MIN, i16::MAX, 16384, -16384, 0, 0] }).unwrap();
        assert_eq!(waveform.duration_sec, 2.0);
        assert_eq!(waveform.peaks, vec![0.0, 1.0, 0.5, 0.0]);
    }

    #[test]
    fn bounds_payload_and_keeps_a_single_sample_attack() {
        let mut audio = DecodedPcm16 { sample_rate_hz: 48000, channels: 1, data: vec![0; 48000] };
        audio.data[12345] = i16::MAX;
        let waveform = summarize(&audio).unwrap();
        assert_eq!(waveform.peaks.len(), 16384);
        assert!(waveform.peaks[12345 * 16384 / 48000] > 0.99);
    }

    #[test]
    fn handles_empty_audio_and_rejects_incomplete_frames() {
        assert!(summarize(&DecodedPcm16 { sample_rate_hz: 48000, channels: 2, data: vec![] }).unwrap().peaks.is_empty());
        assert!(summarize(&DecodedPcm16 { sample_rate_hz: 48000, channels: 2, data: vec![1] }).is_err());
    }
}
