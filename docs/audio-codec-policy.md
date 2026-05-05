# Audio Codec Policy

Decision: host playback uses the in-process Rust/Symphonia decoder.

This resolves the host audio codec choice as option 3: a bundled library decoder for playback. It is not the same decision as ingest decoding.

## Host Playback

- AuralPrimer and AuralStudio decode SongPack playback audio in Rust with Symphonia.
- Supported SongPack playback assets are `audio/mix.ogg`, `audio/mix.mp3`, and `audio/mix.wav`.
- SongPack loading prefers `mix.ogg`, then `mix.mp3`, then `mix.wav`.
- The host must not require FFmpeg or platform-specific audio stack decoders for normal playback.
- WebAudio/browser decode remains only a renderer fallback path when native loading is unavailable.

## Ingest

- The Python ingest sidecar always writes canonical `audio/mix.wav` into generated SongPacks.
- Non-WAV source ingest may use an FFmpeg sidecar for robust conversion to PCM/WAV.
- FFmpeg is packaged and licensed as an ingest dependency, not as the host playback codec layer.

## Rationale

- Rust/Symphonia keeps playback portable and testable across Windows/Linux without depending on user-installed codec packs.
- Avoiding platform decoder APIs reduces OS-specific behavior differences in transport timing.
- Keeping FFmpeg in the sidecar boundary avoids putting a large external process in the playback path.

## Current Limits

- Host playback codec support is limited to the formats enabled by the bundled Symphonia features.
- If a future format is needed, add it to the Symphonia feature set and lock it with decode tests before advertising it in the UI.
