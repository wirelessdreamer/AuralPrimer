"""Parse a MusicXML score into per-role :class:`MelodicNote` lists.

Deliberately stdlib-only (``xml.etree``): MusicXML note extraction needs only
divisions/duration/pitch/tie/chord/backup/forward bookkeeping, and pulling in
music21 would drag its multi-hundred-MB bundled corpus into the frozen sidecar.
The eval cross-checks this parser against music21 to confirm parity.

Timing model (MusicXML 3.x): every ``<note>``'s ``<duration>`` is in *divisions*
(``<divisions>`` per quarter note, from ``<attributes>``). A ``<chord/>`` note
shares the previous note's onset (cursor does not advance). ``<backup>`` /
``<forward>`` move the cursor for multi-voice measures. Tempo comes from
``<sound tempo=…>`` or ``<direction><metronome>``; we build a division→seconds
map that honours tempo changes. Tied notes (``<tie type="start"/stop">``) are
merged into one sustained note.

Compressed ``.mxl`` (zip containing the score XML) is handled transparently.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from aural_ingest.transcription import MelodicNote

_STEP_TO_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_DEFAULT_TEMPO = 120.0
_DEFAULT_VELOCITY = 80

# Part / instrument name (lowercased, substring) -> AuralStudio role.
# Most-specific first.
_NAME_TO_ROLE: tuple[tuple[str, str], ...] = (
    ("acoustic piano", "keys"),
    ("piano", "keys"),
    ("keyboard", "keys"),
    ("synth", "keys"),
    ("organ", "keys"),
    ("electric bass", "bass"),
    ("bass", "bass"),
    ("lead vocal", "vocals"),
    ("backing vocal", "vocals"),
    ("vocal", "vocals"),
    ("voice", "vocals"),
    ("choir", "vocals"),
    ("electric guitar", "rhythm_guitar"),
    ("guitar", "rhythm_guitar"),
    ("drum", "drums"),
    ("percussion", "drums"),
)


def role_for_part_name(name: str | None) -> str:
    n = (name or "").strip().lower()
    for needle, role in _NAME_TO_ROLE:
        if needle in n:
            return role
    return "keys"  # a lone unnamed melodic part is most often the keyboard part


@dataclass
class _PendingNote:
    onset_div: float
    dur_div: float
    pitch: int
    velocity: int


def _read_xml_bytes(path: Path) -> bytes:
    """Return the score XML bytes, transparently unzipping an ``.mxl``."""
    if path.suffix.lower() == ".mxl" or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            # META-INF/container.xml points at the rootfile; fall back to the
            # first .xml/.musicxml that isn't the container.
            root_path = None
            try:
                container = zf.read("META-INF/container.xml")
                croot = ET.fromstring(container)
                rf = croot.find(".//{*}rootfile")
                if rf is not None:
                    root_path = rf.get("full-path")
            except Exception:
                root_path = None
            if root_path is None:
                names = [n for n in zf.namelist()
                         if n.lower().endswith((".xml", ".musicxml"))
                         and not n.startswith("META-INF")]
                root_path = names[0] if names else None
            if root_path is None:
                raise ValueError(f"{path.name}: no score XML inside the .mxl")
            return zf.read(root_path)
    return path.read_bytes()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _pitch_midi(pitch_el: ET.Element) -> int | None:
    step = pitch_el.findtext("{*}step")
    octave = pitch_el.findtext("{*}octave")
    if step is None or octave is None:
        return None
    alter = pitch_el.findtext("{*}alter")
    semis = _STEP_TO_SEMITONE.get(step.upper())
    if semis is None:
        return None
    # MIDI: C4 = 60, and MusicXML octave 4 is the C4 octave -> (octave+1)*12.
    midi = (int(octave) + 1) * 12 + semis + (int(float(alter)) if alter else 0)
    return midi if 0 <= midi <= 127 else None


def _div_to_seconds(div_pos: float, tempo_map: list[tuple[float, float]], divisions: float) -> float:
    """Convert an absolute division position to seconds, honouring tempo changes.

    ``tempo_map`` is a sorted list of ``(division_position, bpm)`` segments.
    """
    if not tempo_map:
        tempo_map = [(0.0, _DEFAULT_TEMPO)]
    seconds = 0.0
    for i, (start_div, bpm) in enumerate(tempo_map):
        end_div = tempo_map[i + 1][0] if i + 1 < len(tempo_map) else float("inf")
        if div_pos <= start_div:
            break
        span_end = min(div_pos, end_div)
        quarters = (span_end - start_div) / divisions
        seconds += quarters * (60.0 / bpm)
        if div_pos <= end_div:
            break
    return seconds


def parse_musicxml(path: str | Path) -> dict[str, list[MelodicNote]]:
    """Parse a MusicXML/.mxl file into ``{role: [MelodicNote, …]}`` in seconds."""
    path = Path(path)
    root = ET.fromstring(_read_xml_bytes(path))

    # part-id -> display name, from <part-list>.
    part_names: dict[str, str] = {}
    for sp in root.findall(".//{*}part-list/{*}score-part"):
        pid = sp.get("id") or ""
        nm = sp.findtext("{*}part-name") or sp.findtext(".//{*}instrument-name") or ""
        part_names[pid] = nm

    # First pass across ALL parts: collect a global tempo map keyed by absolute
    # division position (tempo directions usually live in one part but apply to
    # the whole score).
    out: dict[str, list[MelodicNote]] = {}

    for part in root.findall("{*}part"):
        pid = part.get("id") or ""
        role = role_for_part_name(part_names.get(pid))
        divisions = 1.0
        tempo_map: list[tuple[float, float]] = []
        cursor = 0.0           # absolute division position of the time cursor
        pending: list[_PendingNote] = []
        # tie bookkeeping: pitch -> index into `pending` of the open tied note
        open_ties: dict[int, int] = {}

        for measure in part.findall("{*}measure"):
            for el in measure:
                tag = _local(el.tag)
                if tag == "attributes":
                    d = el.findtext("{*}divisions")
                    if d:
                        divisions = float(d)
                elif tag == "direction":
                    snd = el.find(".//{*}sound[@tempo]")
                    if snd is not None:
                        tempo_map.append((cursor, float(snd.get("tempo"))))
                    else:
                        met = el.find(".//{*}metronome")
                        if met is not None:
                            pm = met.findtext("{*}per-minute")
                            if pm:
                                try:
                                    tempo_map.append((cursor, float(pm)))
                                except ValueError:
                                    pass
                elif tag == "sound" and el.get("tempo"):
                    tempo_map.append((cursor, float(el.get("tempo"))))
                elif tag == "backup":
                    cursor -= float(el.findtext("{*}duration") or 0)
                elif tag == "forward":
                    cursor += float(el.findtext("{*}duration") or 0)
                elif tag == "note":
                    dur = float(el.findtext("{*}duration") or 0)
                    is_chord = el.find("{*}chord") is not None
                    is_rest = el.find("{*}rest") is not None
                    onset = cursor
                    if is_chord and pending:
                        # chord: share the previous note's onset
                        onset = pending[-1].onset_div
                    if not is_rest:
                        pel = el.find("{*}pitch")
                        midi = _pitch_midi(pel) if pel is not None else None
                        if midi is not None:
                            tie_types = {t.get("type") for t in el.findall("{*}tie")}
                            if "stop" in tie_types and midi in open_ties:
                                # extend the open tied note instead of adding one
                                pending[open_ties[midi]].dur_div += dur
                                if "start" not in tie_types:
                                    open_ties.pop(midi, None)
                            else:
                                pending.append(_PendingNote(onset, dur, midi, _DEFAULT_VELOCITY))
                                if "start" in tie_types:
                                    open_ties[midi] = len(pending) - 1
                    # advance cursor only for non-chord notes
                    if not is_chord:
                        cursor += dur

        if not tempo_map:
            tempo_map = [(0.0, _DEFAULT_TEMPO)]
        tempo_map.sort(key=lambda x: x[0])

        notes: list[MelodicNote] = []
        for pn in pending:
            t_on = _div_to_seconds(pn.onset_div, tempo_map, divisions)
            t_off = _div_to_seconds(pn.onset_div + pn.dur_div, tempo_map, divisions)
            if t_off <= t_on:
                t_off = t_on + 1e-3
            notes.append(MelodicNote(t_on=t_on, t_off=t_off, pitch=pn.pitch,
                                     velocity=pn.velocity, instrument=role))
        if notes:
            notes.sort(key=lambda n: (n.t_on, n.pitch))
            out.setdefault(role, []).extend(notes)

    return out


@dataclass
class MusicXmlTimeline:
    """Score-derived timeline for building a pack's beat/measure grid."""

    tempo_bpm: float
    time_signature: tuple[int, int]
    measure_onsets_sec: list[float]  # start time of each notated measure
    duration_sec: float
    movement_title: str | None  # note: Mirelo stamps the KEY here, not a title


def parse_musicxml_timeline(path: str | Path) -> MusicXmlTimeline:
    """Extract tempo, time signature, measure onsets (seconds) and duration.

    Walks the part that has the most measures (usually the piano/keyboard
    staff) so the bar grid is complete. The score gives an exact, metronomic
    grid — better than deriving beats from expressively-timed audio.
    """
    path = Path(path)
    root = ET.fromstring(_read_xml_bytes(path))
    movement_title = (root.findtext("{*}movement-title") or "").strip() or None

    best: MusicXmlTimeline | None = None
    for part in root.findall("{*}part"):
        divisions = 1.0
        tempo_map: list[tuple[float, float]] = []
        cursor = 0.0
        num, den = 4, 4
        measure_starts: list[float] = []
        max_end = 0.0

        for measure in part.findall("{*}measure"):
            measure_starts.append(cursor)
            for el in measure:
                tag = _local(el.tag)
                if tag == "attributes":
                    d = el.findtext("{*}divisions")
                    if d:
                        divisions = float(d)
                    time_el = el.find("{*}time")
                    if time_el is not None:
                        b = time_el.findtext("{*}beats")
                        bt = time_el.findtext("{*}beat-type")
                        if b and bt:
                            num, den = int(b), int(bt)
                elif tag == "direction":
                    snd = el.find(".//{*}sound[@tempo]")
                    if snd is not None:
                        tempo_map.append((cursor, float(snd.get("tempo"))))
                    else:
                        met = el.find(".//{*}metronome")
                        pm = met.findtext("{*}per-minute") if met is not None else None
                        if pm:
                            try:
                                tempo_map.append((cursor, float(pm)))
                            except ValueError:
                                pass
                elif tag == "sound" and el.get("tempo"):
                    tempo_map.append((cursor, float(el.get("tempo"))))
                elif tag == "backup":
                    cursor -= float(el.findtext("{*}duration") or 0)
                elif tag == "forward":
                    cursor += float(el.findtext("{*}duration") or 0)
                elif tag == "note":
                    dur = float(el.findtext("{*}duration") or 0)
                    if el.find("{*}chord") is None:
                        cursor += dur
                    max_end = max(max_end, cursor)

        if not tempo_map:
            tempo_map = [(0.0, _DEFAULT_TEMPO)]
        tempo_map.sort(key=lambda x: x[0])
        onsets = [_div_to_seconds(m, tempo_map, divisions) for m in measure_starts]
        duration = _div_to_seconds(max_end, tempo_map, divisions)
        tl = MusicXmlTimeline(
            tempo_bpm=tempo_map[0][1],
            time_signature=(num, den),
            measure_onsets_sec=onsets,
            duration_sec=duration,
            movement_title=movement_title,
        )
        if best is None or len(tl.measure_onsets_sec) > len(best.measure_onsets_sec):
            best = tl

    if best is None:
        best = MusicXmlTimeline(_DEFAULT_TEMPO, (4, 4), [0.0], 0.0, movement_title)
    return best
