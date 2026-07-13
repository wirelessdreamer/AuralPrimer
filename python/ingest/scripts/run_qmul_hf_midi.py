"""Wrapper for the xavriley hf_midi_transcription CLI.

The upstream CLI prints non-ASCII status glyphs, which can fail under the
default Windows console encoding when launched by the AuralPrimer subprocess
adapter. This wrapper forces replace-on-error UTF-8 output before invoking the
package CLI in-process.
"""

from __future__ import annotations

import argparse
import sys


def _reconfigure_streams() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, help="Input audio file.")
    parser.add_argument("--out-midi", required=True, help="Output MIDI file.")
    parser.add_argument("--instrument", default="guitar", choices=("saxophone", "bass", "guitar", "piano"))
    parser.add_argument("--checkpoint", required=True, help="Local checkpoint path.")
    parser.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda", "mps"))
    parser.add_argument("--batch-size", default="8")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    _reconfigure_streams()

    from hf_midi_transcription.cli import main as cli_main

    forwarded = [
        "midi_transcription",
        args.audio,
        args.out_midi,
        "--instrument",
        args.instrument,
        "--checkpoint",
        args.checkpoint,
        "--device",
        args.device,
        "--batch-size",
        str(args.batch_size),
    ]
    if args.verbose:
        forwarded.append("--verbose")
    sys.argv = forwarded
    try:
        cli_main()
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
