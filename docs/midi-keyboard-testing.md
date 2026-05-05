# MIDI Keyboard Input Testing

This covers hardware keyboard input through the Tauri/midir path. Browser-only Vite mode cannot see native MIDI devices in this app.

## Quick Test

1. Plug in the MIDI keyboard before launching AuralPrimer.
2. Launch the game app through the portable build or `npm run game:dev`.
3. Open `Configure`, then the `MIDI` panel.
4. In `MIDI Input (keyboard + clock follow)`, click `Refresh`, select the keyboard input port, then click `Connect`.
5. Play several white and black keys. The `active notes` monitor should show channel, note name, MIDI pitch, velocity, and sustain-held state.
6. Load a song with `Keys` selected. In piano-roll mode, the bottom 88-key keyboard should light cyan for live keys while the chart notes continue using the existing chart colors.
7. Press and release the sustain pedal. Released notes should remain marked as sustain-held until the pedal is released.
8. Click `Clear active notes` if a device disconnect or driver issue leaves a stuck note.

## Expected Events

- `note_on`: key press with velocity greater than zero.
- `note_off`: key release. Native parsing also normalizes `note_on` with velocity zero into `note_off`.
- `control_change` CC64: sustain pedal. Values `64..127` are down; values `0..63` are up.
- `control_change` CC120 or CC123: panic/all-notes-off for that channel.
- `pitch_bend`, `program_change`, `channel_pressure`, and `poly_aftertouch`: visible in the raw input event monitor for controller compatibility checks.

## Troubleshooting

- Native port enumeration uses WinRT on Windows, CoreMIDI on macOS, and ALSA on Linux. The MIDI panel reports the backend used when ports are found.
- If no ports appear, close other audio/MIDI apps that may have exclusive access to the device, unplug/replug the keyboard, then click `Refresh`.
- On Windows, the portable build uses the modern WinRT MIDI backend because older WinMM enumeration can miss devices that Ableton and other DAWs still show.
- On Linux, confirm ALSA MIDI sequencer support is active and that the user has permission to access the MIDI device. If the device is only visible through JACK/PipeWire bridging, expose it to ALSA or add a dedicated bridge before refreshing.
- If notes appear in the monitor but do not affect gameplay scoring, that is expected right now: the input bus is wired and visible, but scoring/hit-window consumption remains a separate milestone.
- If transport jumps when testing a controller that also sends MIDI clock, disable `follow external clock` unless sync is the thing being tested.
- Keep SysEx disabled unless specifically testing a controller profile; normal keyboard input does not need it.
