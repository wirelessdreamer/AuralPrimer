#!/usr/bin/env python3
"""M-Audio Axiom helper: back up presets, and verify button modes.

Why there is no "program the buttons" command here
--------------------------------------------------
The Axiom accepts configuration only as a SysEx *memory dump* -- an opaque
blob covering all 20 patch locations -- and the user guide is explicit that
restoring one permanently overwrites every preset on the device. M-Audio never
published the layout of that blob, so a generator would be guessing at an
undocumented binary format with all 20 presets as the stake. Hand-programming
one button is five keypresses; that is not a trade worth making.

What this does instead is remove the two real risks of doing it by hand:

  backup   capture the SysEx dump to a file first, so a mistake is undoable
  restore  send a captured dump back (explicitly opt-in; it overwrites)
  check    watch the buttons and report momentary vs toggle, so you can tell
           which ones are converted and which were missed

`check` is the one to keep open while programming. Momentary (controller 146)
sends a value on press and another on release; toggle sends one value per
press and nothing on release. Holding the button for a moment separates them.

Requires mido + python-rtmidi. Nothing here ships with AuralPrimer; run it
with the music-DSP venv:

    D:/Code/AbletonFullControlMCP/.venv/Scripts/python.exe scripts/axiom_tool.py check

Close AuralPrimer (or disconnect its MIDI input) first -- Windows hands the
port to one process at a time.
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import mido
except ImportError:  # pragma: no cover - environment guard
    sys.exit("mido is required:  pip install mido python-rtmidi")

DEFAULT_MATCH = "axiom"
# A held button must out-last this before we call the release missing.
RELEASE_WAIT_SEC = 3.0
# A dump is finished when this much silence follows it.
DUMP_IDLE_SEC = 3.0


def pick_port(names: list[str], match: str, kind: str) -> str:
    hits = [n for n in names if match.lower() in n.lower()]
    if not hits:
        available = ", ".join(names) if names else "(none)"
        sys.exit(
            "No {} port matching {!r}.\nAvailable: {}\n"
            "Plug the controller in, and close any app holding the port.".format(kind, match, available)
        )
    return hits[0]


def open_input(match: str):
    name = pick_port(mido.get_input_names(), match, "input")
    try:
        port = mido.open_input(name)
    except OSError as exc:
        sys.exit(
            "Could not open {!r}: {}\n"
            "Close AuralPrimer (or disconnect its MIDI input) and retry.".format(name, exc)
        )
    print("listening on: {}".format(name))
    return port


def cmd_check(args: argparse.Namespace) -> int:
    """Classify each button as momentary or toggle from what it transmits."""
    port = open_input(args.match)
    print()
    print("Press and HOLD a button for about a second, then release.")
    print("  momentary (146) -> a value on press, then another on release")
    print("  toggle          -> one value per press, nothing on release")
    print("Ctrl+C when done.")
    print()

    pending: dict[tuple[int, int], float] = {}  # (channel, cc) -> time of press
    verdicts: dict[tuple[int, int], str] = {}

    def expire(now: float) -> None:
        for key, pressed_at in list(pending.items()):
            if now - pressed_at >= RELEASE_WAIT_SEC:
                ch, cc = key
                verdicts[key] = "TOGGLE"
                print("  CC {:<3} ch{}  ->  TOGGLE     (no release; the off value waits for your next press)".format(cc, ch + 1))
                del pending[key]

    try:
        while True:
            for msg in port.iter_pending():
                if msg.type != "control_change":
                    continue
                key = (msg.channel, msg.control)
                ch, cc = key
                now = time.monotonic()
                if msg.value > 0:
                    pending[key] = now
                else:
                    pressed_at = pending.pop(key, None)
                    if pressed_at is None:
                        # A zero with no recent press is the second half of a
                        # toggle pair: the off value of an earlier press.
                        verdicts[key] = "TOGGLE"
                        print("  CC {:<3} ch{}  ->  TOGGLE     (off value arrived on a later press)".format(cc, ch + 1))
                    else:
                        verdicts[key] = "MOMENTARY"
                        print("  CC {:<3} ch{}  ->  MOMENTARY  (released after {:.2f}s)".format(cc, ch + 1, now - pressed_at))
            expire(time.monotonic())
            time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        port.close()

    if verdicts:
        print()
        print("summary")
        for (ch, cc), verdict in sorted(verdicts.items()):
            note = "" if verdict == "MOMENTARY" else "   <- set this one to controller 146"
            print("  CC {:<3} ch{}  {}{}".format(cc, ch + 1, verdict, note))
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Capture a SysEx memory dump to a .syx file."""
    port = open_input(args.match)
    print()
    print("On the Axiom:  press Edit, then the Memory Dump key.")
    print("The screen shows SYS while it transmits.")
    print()

    messages: list = []
    last = time.monotonic()
    try:
        while True:
            for msg in port.iter_pending():
                if msg.type == "sysex":
                    messages.append(msg)
                    last = time.monotonic()
                    print("  captured block {} ({} bytes)".format(len(messages), len(msg.data)))
            if messages and time.monotonic() - last >= DUMP_IDLE_SEC:
                break
            time.sleep(0.005)
    except KeyboardInterrupt:
        print()
        print("interrupted")
    finally:
        port.close()

    if not messages:
        print("Nothing captured. Is SysEx reaching this port? Try the other Axiom input.")
        return 1
    mido.write_syx_file(args.file, messages)
    total = sum(len(m.data) for m in messages)
    print()
    print("wrote {}  ({} blocks, {} bytes)".format(args.file, len(messages), total))
    print("Keep this. `restore` puts it back if programming goes wrong.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Send a previously captured dump back to the Axiom."""
    try:
        messages = mido.read_syx_file(args.file)
    except OSError as exc:
        sys.exit("Cannot read {}: {}".format(args.file, exc))
    if not messages:
        sys.exit("{} contains no SysEx.".format(args.file))
    print("{}: {} block(s), {} bytes".format(args.file, len(messages), sum(len(m.data) for m in messages)))
    if not args.yes:
        sys.exit(
            "This OVERWRITES ALL 20 presets on the device, permanently.\n"
            "Re-run with --yes if that is what you want."
        )
    name = pick_port(mido.get_output_names(), args.match, "output")
    with mido.open_output(name) as port:
        print("sending to: {}".format(name))
        for i, msg in enumerate(messages, 1):
            port.send(msg)
            print("  sent block {}/{}".format(i, len(messages)))
            time.sleep(args.delay)
    print()
    print("Done. Load a preset or power-cycle the Axiom for it to take effect.")
    return 0


def cmd_ports(_: argparse.Namespace) -> int:
    print("inputs:")
    for name in mido.get_input_names():
        print("  {}".format(name))
    print("outputs:")
    for name in mido.get_output_names():
        print("  {}".format(name))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--match", default=DEFAULT_MATCH, help="substring of the MIDI port name (default: axiom)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list MIDI ports").set_defaults(func=cmd_ports)
    sub.add_parser("check", help="report momentary vs toggle per button").set_defaults(func=cmd_check)

    backup = sub.add_parser("backup", help="capture a SysEx memory dump to a file")
    backup.add_argument("file", help="output .syx path")
    backup.set_defaults(func=cmd_backup)

    restore = sub.add_parser("restore", help="send a captured dump back (OVERWRITES all presets)")
    restore.add_argument("file", help="input .syx path")
    restore.add_argument("--yes", action="store_true", help="confirm the overwrite")
    restore.add_argument("--delay", type=float, default=0.15, help="seconds between blocks (default 0.15)")
    restore.set_defaults(func=cmd_restore)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
