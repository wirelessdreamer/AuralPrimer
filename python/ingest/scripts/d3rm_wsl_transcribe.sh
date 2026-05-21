#!/usr/bin/env bash
# D3RM piano-transcription wrapper, invoked from Windows via:
#   wsl --distribution Debian bash /mnt/d/AuralPrimer/.../d3rm_wsl_transcribe.sh \
#     "<windows-audio-path>" "<windows-midi-out-path>" "<windows-ckpt-path>" "<windows-cfg-path>"
#
# Translates Windows paths (D:\foo) to WSL paths (/mnt/d/foo), activates the
# dedicated D3RM venv, and runs the D3RM CLI against a single wav.
#
# The D3RM venv must be set up once (see SETUP-D3RM.md). Override the venv
# location with AURAL_PIANO_D3RM_VENV (defaults to ~/.venvs/d3rm).
# Override the repo location with AURAL_PIANO_D3RM_REPO (defaults to
# /mnt/d/Code/d3rm).
set -euo pipefail

if [[ $# -lt 4 ]]; then
    echo "usage: $0 <audio> <midi-out> <checkpoint> <config>" >&2
    exit 2
fi

AUDIO_WIN="$1"
MIDI_WIN="$2"
CKPT_WIN="$3"
CFG_WIN="$4"

# Convert Windows-style paths to WSL paths if necessary.
win_to_wsl() {
    local p="$1"
    if [[ "$p" =~ ^([A-Za-z]):[\\/](.*)$ ]]; then
        local drive="${BASH_REMATCH[1],,}"
        local rest="${BASH_REMATCH[2]//\\//}"
        printf '/mnt/%s/%s\n' "$drive" "$rest"
    else
        printf '%s\n' "$p"
    fi
}

AUDIO="$(win_to_wsl "$AUDIO_WIN")"
MIDI="$(win_to_wsl "$MIDI_WIN")"
CKPT="$(win_to_wsl "$CKPT_WIN")"
CFG="$(win_to_wsl "$CFG_WIN")"

VENV="${AURAL_PIANO_D3RM_VENV:-$HOME/.venvs/d3rm}"
REPO="${AURAL_PIANO_D3RM_REPO:-/mnt/d/Code/d3rm}"

if [[ ! -d "$VENV" ]]; then
    echo "D3RM venv not found at $VENV. Run SETUP-D3RM.md instructions first." >&2
    exit 3
fi
if [[ ! -d "$REPO" ]]; then
    echo "D3RM repo not found at $REPO." >&2
    exit 4
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

cd "$REPO"
python main_cli.py test \
    -c "$CFG" \
    --ckpt_path "$CKPT" \
    --trainer.devices 1 \
    --data.test_wav "$AUDIO" \
    --data.test_midi_out "$MIDI"
