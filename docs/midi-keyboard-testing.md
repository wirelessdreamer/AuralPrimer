# MIDI Keyboard Input Testing

This covers hardware keyboard input through the Tauri/midir path. Browser-only Vite mode cannot see native MIDI devices in this app.

For day-to-day setup — connecting a port, binding transport buttons with MIDI
learn, and the momentary-vs-toggle requirement for hold-to-jog — see
[Playing with a MIDI keyboard](../README.md#playing-with-a-midi-keyboard) in
the README. This document stays focused on verifying the input path itself.

## Quick Test

1. Plug in the MIDI keyboard before launching AuralPrimer.
2. Launch the game app through the portable build or `npm run game:dev`.
3. Open `Configure`, then the `MIDI` panel.
4. In `MIDI Input (keyboard + clock follow)`, click `Refresh`, select the keyboard input port, then click `Connect`.
5. Play several white and black keys. The `active notes` monitor should show channel, note name, MIDI pitch, velocity, and sustain-held state.
6. Load a song with `Keys` selected. In piano-roll mode, the bottom 88-key keyboard should light cyan for live keys while the chart notes continue using the existing chart colors.
7. Press and release the sustain pedal. Released notes should remain marked as sustain-held until the pedal is released.
8. Click `Clear active notes` if a device disconnect or driver issue leaves a stuck note.

## Studio and game together

1. Connect the keyboard once in `Configure -> MIDI`. Launch Studio and the game
   from the same portable folder so both use its shared `settings.json`.
2. In Studio Cleanup, enable **Split + game**. Play a chord: cyan pitch rows
   should appear over the spectrogram and the matching game keys should light.
   The chart and **Save** state must remain unchanged.
3. Enable **Hear keyboard**. Verify velocity changes the loudness, key release
   stops the note, and CC64 sustain holds it until the pedal comes up.
4. Pause, seek and resume the song while holding a key. Live keyboard sound
   should remain independent of the song transport and headphone A/B routing.
5. Switch to the game. Studio's held sound should release; new key presses
   should sound once through the game. Switch back and repeat in Studio.
6. Use **All notes off** in Cleanup, disconnect the input, or leave Cleanup:
   live highlights and held sound should clear.
7. Save a Studio MIDI setting and relaunch the game. Previously learned game
   transport bindings must still be present.

Concurrent WinRT opens of the USB Axiom 61 have been verified with Windows MIDI
Services running. Other drivers may impose exclusive access; the status and
**Reconnect** control expose that failure without selecting an unrelated port.

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
