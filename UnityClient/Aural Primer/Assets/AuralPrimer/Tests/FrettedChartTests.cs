// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Reading a guitar or bass CHART, in the shape the desktop's buildChart sends
// (docs/mr-link-protocol.md §4, "Fretted parts").

using System.Collections.Generic;
using AuralPrimer.Link;
using NUnit.Framework;

namespace AuralPrimer.Link.Tests
{
    public class FrettedChartTests
    {
        // What apps/game/src/mrLinkPanel.ts buildChart produces for a lead part,
        // trimmed to the fields that matter here.
        const string LeadChart = @"{
  ""songId"": ""D:\\songs\\riff.feedpak"",
  ""title"": ""Riff"",
  ""keySignature"": null,
  ""nashville"": false,
  ""handMode"": ""both"",
  ""showOtherHand"": true,
  ""tempoMap"": [{ ""tSec"": 0, ""bpm"": 120, ""beatsPerBar"": 4 }],
  ""role"": ""lead_guitar"",
  ""tuning"": [40, 45, 50, 55, 59, 64],
  ""anchors"": [{ ""t"": 0.5, ""fret"": 5, ""width"": 4 }, { ""t"": 4, ""fret"": 7 }],
  ""notes"": [
    { ""on"": 1, ""off"": 1.5, ""pitch"": 45, ""vel"": 0.7, ""s"": 1, ""f"": 0,
      ""finger"": 1, ""chord"": ""A5 {open}"", ""tech"": [""pm""] },
    { ""on"": 1, ""off"": 1.5, ""pitch"": 52, ""vel"": 0.7, ""s"": 2, ""f"": 2, ""finger"": 3, ""chord"": ""A5 {open}"" },
    { ""on"": 2, ""off"": 2.25, ""pitch"": 69, ""vel"": 0.7, ""s"": 4, ""f"": 10, ""tech"": [""ho"", ""sl""] },
    { ""on"": 3, ""off"": 3.25, ""pitch"": 64, ""vel"": 0.7 }
  ]
}";

        [Test]
        public void ReadsStringsFretsAndTheTabsExtras()
        {
            var chart = FrettedChart.Parse(LeadChart);

            Assert.IsNotNull(chart);
            Assert.AreEqual("lead_guitar", chart.Role);
            Assert.AreEqual(new[] { 40, 45, 50, 55, 59, 64 }, chart.Tuning);
            Assert.IsTrue(chart.IsFretted);
            Assert.AreEqual(4, chart.Notes.Count);

            var open = chart.Notes[0].Fret == 0 ? chart.Notes[0] : chart.Notes[1];
            Assert.AreEqual(1, open.String);
            Assert.AreEqual(1, open.Finger);
            // A brace in a chord name is just text, not the end of the note.
            Assert.AreEqual("A5 {open}", open.Chord);
            Assert.AreEqual(new[] { "pm" }, open.Tech);

            Assert.AreEqual(new[] { "ho", "sl" }, chart.Notes[2].Tech);
            Assert.AreEqual(10, chart.Notes[2].Fret);
        }

        [Test]
        public void LeavesANoteTheTabNeverPlacedOffTheNeck()
        {
            var unplaced = FrettedChart.Parse(LeadChart).Notes[3];
            Assert.IsFalse(unplaced.IsPlaced);
            Assert.AreEqual(-1, unplaced.String);
            Assert.AreEqual(-1, unplaced.Fret);
        }

        [Test]
        public void FramesNotesStruckTogetherAsOneChord()
        {
            var notes = FrettedChart.Parse(LeadChart).Notes;
            Assert.AreEqual(notes[0].ChordGroup, notes[1].ChordGroup);
            Assert.That(notes[0].ChordGroup, Is.GreaterThanOrEqualTo(0));
            Assert.AreEqual(-1, notes[2].ChordGroup, "a single note is not a chord");
        }

        [Test]
        public void ReadsTheTabsHandPositionsAndDefaultsTheirWidth()
        {
            var chart = FrettedChart.Parse(LeadChart);
            Assert.IsFalse(chart.AnchorsDerived);
            Assert.AreEqual(2, chart.Anchors.Count);
            Assert.AreEqual(4, chart.Anchors[1].Width);

            // Before the first position, the first; then whichever is in force.
            Assert.AreEqual(5, chart.AnchorAt(0f).Fret);
            Assert.AreEqual(5, chart.AnchorAt(3.9f).Fret);
            Assert.AreEqual(7, chart.AnchorAt(10f).Fret);
            Assert.AreEqual(8, chart.AnchorAt(0.5f).LastFret);
        }

        [Test]
        public void WorksOutHandPositionsWhenThePackHasNone()
        {
            // The mock-up's rule: a four-fret window that slides down to a note
            // below it and up just far enough to reach one above it. Open
            // strings never move the hand.
            var notes = new List<FrettedChart.Note>
            {
                new(1f, 1.1f, 43, 0, 3),
                new(2f, 2.1f, 45, 0, 5),
                new(2.5f, 2.6f, 40, 0, 0),
                new(3f, 3.1f, 49, 0, 9),
                new(4f, 4.1f, 42, 0, 2),
            };

            var anchors = FrettedChart.DeriveAnchors(notes);

            Assert.AreEqual(new[] { 1, 2, 6, 2 }, anchors.ConvertAll(a => a.Fret).ToArray());
            Assert.AreEqual(0.75f, anchors[0].T, 1e-6f, "a quarter-second ahead of the note");

            var chart = FrettedChart.Parse(
                "{\"role\":\"bass\",\"tuning\":[28,33,38,43],\"notes\":[{\"on\":1,\"off\":2,\"pitch\":33,\"s\":0,\"f\":5}]}");
            Assert.IsTrue(chart.AnchorsDerived);
            Assert.AreEqual(2, chart.AnchorAt(5f).Fret);
        }

        [Test]
        public void IsNotFrettedWithoutATuningOrPlacedNotes()
        {
            // A keys chart, or a guitar part from a host too old to place it.
            var keys = FrettedChart.Parse(
                "{\"role\":\"keys\",\"notes\":[{\"on\":0,\"off\":1,\"pitch\":60,\"vel\":0.8}]}");
            Assert.IsNotNull(keys);
            Assert.IsFalse(keys.IsFretted);
            Assert.IsFalse(FrettedChart.IsFrettedRole(keys.Role));
            Assert.IsTrue(FrettedChart.IsFrettedRole("rhythm_guitar"));
        }

        [Test]
        public void RefusesSomethingThatIsNotJson()
        {
            Assert.IsNull(FrettedChart.Parse("{\"notes\":[{\"on\":1,"));
            Assert.IsNull(FrettedChart.Parse("[]"));
        }

        [Test]
        public void ReadsJsonEscapesAndNumbers()
        {
            var value = (Dictionary<string, object>)MiniJson.Parse(
                "{\"a\":\"x\\\"y\\\\z\\u00e9\",\"n\":[-1.5e2, 0, true, null]}");
            Assert.AreEqual("x\"y\\z\u00e9", value["a"]);
            var n = (List<object>)value["n"];
            Assert.AreEqual(-150.0, n[0]);
            Assert.AreEqual(true, n[2]);
            Assert.IsNull(n[3]);
            Assert.Throws<System.FormatException>(() => MiniJson.Parse("{} trailing"));
        }
    }
}
