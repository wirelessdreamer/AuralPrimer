use std::fs;
use std::path::Path;

#[derive(Debug, Clone)]
pub struct WavPcm16 {
    pub sample_rate: u32,
    pub channels: u16,
    pub data: Vec<i16>, // interleaved
}

pub fn read_wav_pcm16(path: &Path) -> Result<WavPcm16, String> {
    let bytes = fs::read(path).map_err(|e| format!("read {}: {e}", path.display()))?;
    read_wav_pcm16_bytes(&bytes)
}

pub fn read_wav_pcm16_bytes(bytes: &[u8]) -> Result<WavPcm16, String> {
    if bytes.len() < 44 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return Err("not a RIFF WAVE".to_string());
    }

    let mut off = 12;
    let mut channels: Option<u16> = None;
    let mut sample_rate: Option<u32> = None;
    let mut bits: Option<u16> = None;
    let mut data: Option<Vec<u8>> = None;

    while off + 8 <= bytes.len() {
        let id = &bytes[off..off + 4];
        let size = u32::from_le_bytes(bytes[off + 4..off + 8].try_into().unwrap()) as usize;
        off += 8;
        if off + size > bytes.len() {
            break;
        }

        if id == b"fmt " {
            if size < 16 {
                return Err("wav fmt chunk too small".to_string());
            }
            let audio_format = u16::from_le_bytes(bytes[off..off + 2].try_into().unwrap());
            if audio_format != 1 {
                return Err(format!(
                    "unsupported wav format (expected PCM=1, got {audio_format})"
                ));
            }
            channels = Some(u16::from_le_bytes(
                bytes[off + 2..off + 4].try_into().unwrap(),
            ));
            sample_rate = Some(u32::from_le_bytes(
                bytes[off + 4..off + 8].try_into().unwrap(),
            ));
            bits = Some(u16::from_le_bytes(
                bytes[off + 14..off + 16].try_into().unwrap(),
            ));
        } else if id == b"data" {
            data = Some(bytes[off..off + size].to_vec());
        }

        off += size + (size % 2);
        if channels.is_some() && sample_rate.is_some() && bits.is_some() && data.is_some() {
            break;
        }
    }

    let channels = channels.ok_or_else(|| "wav missing channels".to_string())?;
    let sample_rate = sample_rate.ok_or_else(|| "wav missing sample_rate".to_string())?;
    let bits = bits.ok_or_else(|| "wav missing bits_per_sample".to_string())?;
    if bits != 16 {
        return Err(format!(
            "unsupported wav bit depth (expected 16, got {bits})"
        ));
    }
    let data = data.ok_or_else(|| "wav missing data chunk".to_string())?;
    if data.len() % 2 != 0 {
        return Err("wav data not aligned to i16".to_string());
    }
    let mut out: Vec<i16> = Vec::with_capacity(data.len() / 2);
    for c in data.chunks_exact(2) {
        out.push(i16::from_le_bytes([c[0], c[1]]));
    }

    Ok(WavPcm16 {
        sample_rate,
        channels,
        data: out,
    })
}

pub fn write_wav_pcm16(path: &Path, wav: &WavPcm16) -> Result<(), String> {
    let channels = wav.channels as u32;
    let byte_rate = wav.sample_rate * channels * 2;
    let block_align = (channels * 2) as u16;
    let data_bytes = (wav.data.len() * 2) as u32;

    let mut bytes: Vec<u8> = vec![];
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_bytes).to_le_bytes());
    bytes.extend_from_slice(b"WAVE");

    bytes.extend_from_slice(b"fmt ");
    bytes.extend_from_slice(&(16u32).to_le_bytes());
    bytes.extend_from_slice(&(1u16).to_le_bytes()); // PCM
    bytes.extend_from_slice(&(wav.channels).to_le_bytes());
    bytes.extend_from_slice(&(wav.sample_rate).to_le_bytes());
    bytes.extend_from_slice(&(byte_rate).to_le_bytes());
    bytes.extend_from_slice(&(block_align).to_le_bytes());
    bytes.extend_from_slice(&(16u16).to_le_bytes());

    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&(data_bytes).to_le_bytes());
    bytes.reserve(wav.data.len() * 2);
    for s in &wav.data {
        bytes.extend_from_slice(&s.to_le_bytes());
    }

    fs::write(path, bytes).map_err(|e| format!("write {}: {e}", path.display()))
}

pub fn mix_wavs(wavs: &[WavPcm16]) -> Result<WavPcm16, String> {
    if wavs.is_empty() {
        return Err("no stems to mix".to_string());
    }
    let sr = wavs[0].sample_rate;
    let ch = wavs[0].channels;
    let n = wavs[0].data.len();
    for w in wavs {
        if w.sample_rate != sr {
            return Err("stem sample rates do not match".to_string());
        }
        if w.channels != ch {
            return Err("stem channel counts do not match".to_string());
        }
        if w.data.len() != n {
            return Err("stem durations do not match".to_string());
        }
    }

    let mut out: Vec<i16> = vec![0; n];
    for i in 0..n {
        let mut acc: i32 = 0;
        for w in wavs {
            acc += w.data[i] as i32;
        }
        acc = acc.clamp(i16::MIN as i32, i16::MAX as i32);
        out[i] = acc as i16;
    }

    Ok(WavPcm16 {
        sample_rate: sr,
        channels: ch,
        data: out,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wav(sr: u32, ch: u16, data: &[i16]) -> WavPcm16 {
        WavPcm16 {
            sample_rate: sr,
            channels: ch,
            data: data.to_vec(),
        }
    }

    #[test]
    fn read_wav_rejects_non_wave_bytes() {
        let err = read_wav_pcm16_bytes(b"not-wave").unwrap_err();
        assert!(err.contains("not a RIFF WAVE"));
    }

    #[test]
    fn write_then_read_round_trip() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join("x.wav");
        let src = wav(48_000, 2, &[1, -2, 3, -4, 5, -6]);
        write_wav_pcm16(&p, &src).unwrap();

        let got = read_wav_pcm16(&p).unwrap();
        assert_eq!(got.sample_rate, 48_000);
        assert_eq!(got.channels, 2);
        assert_eq!(got.data, src.data);
    }

    #[test]
    fn mix_wavs_errors_on_mismatched_shapes() {
        let a = wav(48_000, 1, &[0, 1, 2]);
        let b = wav(44_100, 1, &[0, 1, 2]);
        assert!(mix_wavs(&[a.clone(), b])
            .unwrap_err()
            .contains("sample rates"));

        let c = wav(48_000, 2, &[0, 1, 2]);
        assert!(mix_wavs(&[a.clone(), c])
            .unwrap_err()
            .contains("channel counts"));

        let d = wav(48_000, 1, &[0, 1]);
        assert!(mix_wavs(&[a, d]).unwrap_err().contains("durations"));
    }

    #[test]
    fn mix_wavs_saturates_sum() {
        let a = wav(48_000, 1, &[30_000, -30_000]);
        let b = wav(48_000, 1, &[30_000, -30_000]);
        let m = mix_wavs(&[a, b]).unwrap();
        assert_eq!(m.data, vec![i16::MAX, i16::MIN]);
    }
}
