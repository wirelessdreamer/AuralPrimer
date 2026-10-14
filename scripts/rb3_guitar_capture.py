#!/usr/bin/env python3
"""Record what a Rock Band 3 pro guitar actually sends over MIDI.

The RB3 pro guitars (Fender Mustang, Squier Stratocaster) are not ordinary
MIDI guitars: the game reads their fret and strum state from SysEx, and the
note messages alone do not carry everything. Before decoding the Squier in the
game, this records a real session -- every message, SysEx included -- with
timestamps, and prints each one decoded two ways:

* notes: channel 1 = high E (the Mustang/Jamstik convention), fret = note
  minus the string's standard-tuning open note;
* SysEx: raw bytes, and a decode against the Mustang layout reverse-engineered
  in Linux Journal (Sep 2011): F0 08 40 0A <type> <string> <note> F7, where
  type 1 = fret position and 5 = string played.

A message the decode does not fit is printed raw, which is the point: the
capture is the evidence, the decode is only a first guess.

Requires mido + python-rtmidi; nothing here ships with AuralPrimer. Close
AuralPrimer first -- python-rtmidi opens ports through WinMM, which hands an
input to one program at a time.

    D:/Code/AbletonFullControlMCP/.venv/Scripts/python.exe scripts/rb3_guitar_capture.py list
    D:/Code/AbletonFullControlMCP/.venv/Scripts/python.exe scripts/rb3_guitar_capture.py capture --port "USB" --seconds 150 --out capture.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time

try:
    import mido
except ImportError:  # pragma: no cover - environment guard
    sys.exit("needs mido + python-rtmidi (see the module docstring for the venv to use)")

# Standard tuning, string 1 (high E) first, as the guitar numbers them.
OPEN_BY_STRING = {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40}
STRING_NAME = {1: "e", 2: "B", 3: "G", 4: "D", 5: "A", 6: "E"}
MUSTANG_PREFIX = (0x08, 0x40, 0x0A)
MUSTANG_TYPES = {1: "fret", 5: "strum"}


def describe_note(channel0: int, note: int) -> str:
    string = channel0 + 1
    if string not in OPEN_BY_STRING:
        return f"ch{string}: not a string channel"
    fret = note - OPEN_BY_STRING[string]
    where = f"fret {fret}" if 0 <= fret <= 24 else f"fret {fret}?? (outside 0-24)"
    return f"string {string} ({STRING_NAME[string]}), {where}"


def describe_sysex(data: tuple[int, ...]) -> str:
    """`data` is the body between F0 and F7, as mido gives it."""
    if len(data) == 6 and tuple(data[:3]) == MUSTANG_PREFIX:
        kind, chan, note = data[3], data[4], data[5]
        name = MUSTANG_TYPES.get(kind, f"type {kind}?")
        return f"Mustang layout: {name}, ch byte {chan} -> {describe_note(chan - 1, note) if chan else 'ch 0??'}, note {note}"
    return "does not fit the Mustang layout"


def cmd_list(_: argparse.Namespace) -> int:
    for name in mido.get_input_names():
        print(name)
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    names = mido.get_input_names()
    matches = [n for n in names if args.port.lower() in n.lower()]
    if len(matches) != 1:
        print(f"--port {args.port!r} matched {matches or 'nothing'}; inputs are: {names}", file=sys.stderr)
        return 2
    port_name = matches[0]
    out = open(args.out, "w", encoding="utf-8") if args.out else None
    t0 = time.perf_counter()
    count = 0
    print(f"recording {port_name} for {args.seconds}s ...", flush=True)
    with mido.open_input(port_name) as port:
        while time.perf_counter() - t0 < args.seconds:
            for msg in port.iter_pending():
                t = time.perf_counter() - t0
                raw = msg.bytes()
                if msg.type in ("note_on", "note_off"):
                    off = msg.type == "note_off" or msg.velocity == 0
                    text = f"{'OFF' if off else 'ON '} ch{msg.channel + 1} note {msg.note} vel {msg.velocity}  -> {describe_note(msg.channel, msg.note)}"
                elif msg.type == "sysex":
                    text = f"SYSEX {' '.join(f'{b:02X}' for b in raw)}  dec {list(msg.data)}  -> {describe_sysex(msg.data)}"
                else:
                    text = f"{msg.type} {' '.join(f'{b:02X}' for b in raw)}  {msg}"
                print(f"{t:8.3f}  {text}", flush=True)
                if out:
                    out.write(json.dumps({"t": round(t, 4), "type": msg.type, "bytes": raw}) + "\n")
                count += 1
            time.sleep(0.001)
    if out:
        out.close()
    print(f"done: {count} messages", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list MIDI inputs").set_defaults(fn=cmd_list)
    c = sub.add_parser("capture", help="record and decode a session")
    c.add_argument("--port", required=True, help="substring of the input port name")
    c.add_argument("--seconds", type=float, default=120.0)
    c.add_argument("--out", help="also write each message as JSON lines here")
    c.set_defaults(fn=cmd_capture)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
