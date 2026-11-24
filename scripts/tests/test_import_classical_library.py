"""Focused timing and integration gates for the classical-library installer."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import mido

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("classical_installer", ROOT / "scripts/import_classical_library.py")
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


class ClassicalTimingTests(unittest.TestCase):
    def make_score(self, root, *, extra_meta=(), notes=((0, 1920, 60),)):
        path = Path(root) / "source.mid"
        mid = mido.MidiFile(ticks_per_beat=480)
        conductor = mido.MidiTrack()
        for tick, msg in sorted([(0, mido.MetaMessage("set_tempo", tempo=500000)), *extra_meta],
                                key=lambda row: row[0]):
            previous = sum(m.time for m in conductor)
            conductor.append(msg.copy(time=tick - previous))
        mid.tracks.append(conductor)
        for onset, release, pitch in notes:
            mid.tracks.append(mido.MidiTrack([
                mido.Message("note_on", channel=0, note=pitch, velocity=90, time=onset),
                mido.Message("note_off", channel=0, note=pitch, time=release - onset)]))
        mid.save(path)
        return installer.Score(path)

    def test_tempo_change_inside_beat_does_not_restart_grid(self):
        with tempfile.TemporaryDirectory() as root:
            score = self.make_score(root, extra_meta=[
                (240, mido.MetaMessage("set_tempo", tempo=1000000))])
            timeline, _ = score.timing()
            self.assertEqual([b["time"] for b in timeline["beats"][:3]], [0.0, 0.75, 1.75])
            self.assertEqual(timeline["tempos"][1], {"time": 0.25, "bpm": 60.0})

    def test_compound_meter_and_meter_change_keep_measure_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            score = self.make_score(root, extra_meta=[
                (0, mido.MetaMessage("time_signature", numerator=6, denominator=8)),
                (1440, mido.MetaMessage("time_signature", numerator=3, denominator=4))],
                notes=[(0, 2880, 60)])
            timeline, measures = score.timing()
            self.assertEqual(measures[0]["ts"], [6, 8])
            self.assertEqual(measures[1]["ts"], [3, 4])
            self.assertEqual(measures[1]["t"], 1.5)
            self.assertEqual([b["time"] for b in timeline["beats"][:8]],
                             [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 2])
            self.assertEqual(timeline["beats"][6]["measure"], 2)

    def test_ensemble_unisons_keep_each_written_note_off(self):
        with tempfile.TemporaryDirectory() as root:
            score = self.make_score(root, notes=[(0, 960, 60), (0, 480, 60), (240, 1440, 60)])
            chart = Path(root) / "chart.mid"
            score.write_chart(chart)
            roundtrip = installer.Score(chart)
            self.assertEqual(roundtrip.notes, score.notes)
            channels = {m.channel for t in roundtrip.midi.tracks for m in t if m.type == "note_on"}
            self.assertEqual(len(channels), 3)
            self.assertFalse({9, 15} & channels)

    def test_percussion_remains_source_only_for_keyboard_chart(self):
        with tempfile.TemporaryDirectory() as root:
            score = self.make_score(root)
            score.midi.tracks.append(mido.MidiTrack([
                mido.Message("note_on", channel=9, note=36, velocity=100),
                mido.Message("note_off", channel=9, note=36, time=120)]))
            path = Path(root) / "with-drums.mid"
            score.midi.save(path)
            parsed = installer.Score(path)
            self.assertEqual(parsed.percussion_notes, 1)
            self.assertEqual(parsed.notes, score.notes)

    def test_catalog_has_40_additional_works_and_complete_seasons(self):
        catalog = json.loads((installer.COLLECTION / "catalog.json").read_text(encoding="utf-8"))
        entries = catalog["entries"]
        required = set(catalog["required_work_ids"])
        self.assertEqual(len({e["work_id"] for e in entries} - required), 40)
        self.assertEqual(len(entries), 60)
        self.assertEqual(len({e["sha256"] for e in entries}), 60)
        for season in ("spring_rv269", "summer_rv315", "autumn_rv293", "winter_rv297"):
            self.assertEqual(sum(e["work_id"] == "vivaldi_" + season for e in entries), 3)
        for entry in entries:
            self.assertTrue(entry["attribution"])
            self.assertTrue(entry["composer"])
            self.assertTrue(entry["license_url"].startswith("https://creativecommons.org/"))
            installer.check_source(entry)


if __name__ == "__main__":
    unittest.main()
