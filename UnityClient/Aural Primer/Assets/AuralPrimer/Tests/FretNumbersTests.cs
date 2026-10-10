// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// visualizers/viz-tab/tests/fretNumbers.test.ts, case for case, against the
// C# port. The two suites pin the same numbers, so the headset and the
// desktop can only disagree about a dot if one of them fails.

using System;
using System.Collections.Generic;
using System.Linq;
using AuralPrimer.Calibration;
using NUnit.Framework;

namespace AuralPrimer.Link.Tests
{
    public class FretNumbersTests
    {
        static readonly int[] Std = { 40, 45, 50, 55, 59, 64 };
        static readonly int[] Bass = { 28, 33, 38, 43 };
        static readonly NumberFrame CMajor = FretNumbers.Frame(new SongKey(0, minor: false));
        static readonly NumberFrame AMinor = FretNumbers.Frame(new SongKey(9, minor: true));

        static readonly string[] Names = { "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B" };
        static int[] Pcs(string names) => names.Split(' ').Select(n => Array.IndexOf(Names, n)).ToArray();

        static string[] Labels(NumberFrame frame, string names) =>
            Pcs(names).Select(pc => FretNumbers.DegreeLabel(frame, pc)).ToArray();

        static int[][] Pairs(PositionInstance inst) =>
            inst.PerString.Select(p => new[] { p.Low, p.High }).ToArray();

        // ---- spelling -----------------------------------------------------

        [Test]
        public void IsOneTablePerCountingAsTheMockUpSpellsIt()
        {
            Assert.AreEqual(new[] { "1", "♭2", "2", "♭3", "3", "4", "♯4", "5", "♭6", "6", "♭7", "7" }, FretNumbers.LabelsMajor);
            Assert.AreEqual(new[] { "1", "♭2", "2", "♭3", "3", "4", "♭5", "5", "♭6", "6", "♭7", "7" }, FretNumbers.LabelsMinor);
        }

        [Test]
        public void NumbersAMajorKeyWithBlueNotesAsFlatThreeAndFlatSeven()
        {
            Assert.AreEqual(new[] { "1", "2", "3", "4", "5", "6", "7" }, Labels(CMajor, "C D E F G A B"));
            Assert.AreEqual(new[] { "♭3", "♭7", "♯4" }, Labels(CMajor, "Eb Bb F#"));
        }

        [Test]
        public void NumbersAMinorKeyFromItsOwnOneRaisedNotesReadingThreeSixSeven()
        {
            Assert.AreEqual(new[] { "1", "2", "♭3", "4", "5", "♭6", "♭7" }, Labels(AMinor, "A B C D E F G"));
            Assert.AreEqual(new[] { "3", "6", "7" }, Labels(AMinor, "C# F# Ab"));
        }

        [Test]
        public void CountsFromTheRelativeOnTheSameNotes()
        {
            Assert.AreEqual("6", FretNumbers.DegreeLabel(CMajor, 9));
            var relative = FretNumbers.Frame(new SongKey(0, minor: false), CountFrom.Relative);
            Assert.AreEqual("1", FretNumbers.DegreeLabel(relative, 9));
            Assert.IsTrue(relative.Minor);
            // And back the other way for a minor song.
            Assert.AreEqual("1", FretNumbers.DegreeLabel(FretNumbers.Frame(new SongKey(9, minor: true), CountFrom.Relative), 0));
            // Geometry never moves with counting.
            Assert.AreEqual(CMajor.BoxRoot, relative.BoxRoot);
        }

        // ---- tiers ----------------------------------------------------------

        [Test]
        public void RingsTheOneFillsTheBoxHollowsTheCompletersAndDashesTheRest()
        {
            Assert.AreEqual(NumberTier.Root, FretNumbers.TierOf(CMajor, 60));
            Assert.That(Pcs("D E G A").Select(pc => FretNumbers.TierOf(CMajor, pc)), Is.All.EqualTo(NumberTier.Box));
            Assert.That(Pcs("F B").Select(pc => FretNumbers.TierOf(CMajor, pc)), Is.All.EqualTo(NumberTier.Mode));
            Assert.That(Pcs("Eb F# Bb").Select(pc => FretNumbers.TierOf(CMajor, pc)), Is.All.EqualTo(NumberTier.Chrom));
            // In A minor the 1 moves, the box does not: C is now a box note.
            Assert.AreEqual(NumberTier.Root, FretNumbers.TierOf(AMinor, 9));
            Assert.AreEqual(NumberTier.Box, FretNumbers.TierOf(AMinor, 0));
        }

        // ---- octave colour ----------------------------------------------------

        [Test]
        public void StartsANewColourAtEachOneCountedUpFromTheLowestString()
        {
            // C major on guitar: E2 and B2 are below the first C, C3 starts the next band.
            Assert.AreEqual(new[] { 0, 0, 1, 1, 2, 3 },
                new[] { 40, 47, 48, 59, 60, 72 }.Select(m => FretNumbers.OctaveBand(CMajor, 40, m)).ToArray());
            // A minor: the band turns over at each A.
            Assert.AreEqual(new[] { 0, 0, 1, 2 },
                new[] { 40, 44, 45, 57 }.Select(m => FretNumbers.OctaveBand(AMinor, 40, m)).ToArray());
            // Clamped to the palette.
            Assert.AreEqual(FretNumbers.OctaveColors.Length - 1, FretNumbers.OctaveBand(CMajor, 40, 120));
        }

        // ---- positions ------------------------------------------------------------

        [Test]
        public void PutsPositionOneAsTheMinorBoxRootedOnTheLowEString()
        {
            var p1 = FretNumbers.Position(CMajor, Std, 1, 0);
            Assert.AreEqual(new[] { 5, 8 }, new[] { p1.Lo, p1.Hi });
            Assert.AreEqual(new[] { new[] { 5, 8 }, new[] { 5, 7 }, new[] { 5, 7 }, new[] { 5, 7 }, new[] { 5, 8 }, new[] { 5, 8 } },
                            Pairs(p1));
            // The scale-completers inside its frets: 2 on low E 7, ♭6 on A 8 and B 6, 2 on high e 7.
            Assert.AreEqual(new[] { new[] { 0, 7 }, new[] { 1, 8 }, new[] { 4, 6 }, new[] { 5, 7 } },
                            p1.Mode.Select(n => new[] { n.S, n.F }).ToArray());
        }

        [Test]
        public void PlacesOneBetweenFiveBelowAndTwoAbove()
        {
            Assert.AreEqual((5, 2), FretNumbers.NeighbourPositions(1));
            Assert.AreEqual((4, 1), FretNumbers.NeighbourPositions(5));
            var p1 = FretNumbers.Position(CMajor, Std, 1, 0);
            var around = FretNumbers.Neighbours(CMajor, Std, p1, 24);
            Assert.AreEqual(new[] { 5, 2, 5 }, new[] { around[0].K, around[0].Lo, around[0].Hi });
            Assert.AreEqual(new[] { 2, 7, 10 }, new[] { around[1].K, around[1].Lo, around[1].Hi });
        }

        [Test]
        public void FindsEveryCopyThatFitsTheNeckAndTheOneNearestAFret()
        {
            Assert.AreEqual(new[] { 5, 17 }, FretNumbers.Positions(CMajor, Std, 1, 24).Select(i => i.Lo).ToArray());
            Assert.AreEqual(17, FretNumbers.InstanceNear(CMajor, Std, 1, 16, 24).Lo);
        }

        [Test]
        public void WorksOnAFourStringBass()
        {
            var p1 = FretNumbers.Position(CMajor, Bass, 1, 0);
            Assert.AreEqual(new[] { new[] { 5, 8 }, new[] { 5, 7 }, new[] { 5, 7 }, new[] { 5, 7 } }, Pairs(p1));
        }

        [Test]
        public void MapsAHandWindowToTheBoxItOverlapsMostElsePositionOne()
        {
            Assert.AreEqual(1, FretNumbers.PositionForHand(CMajor, Std, 5, 4, 24).K);
            Assert.AreEqual(5, FretNumbers.PositionForHand(CMajor, Std, 2, 4, 24).K);
            Assert.AreEqual(2, FretNumbers.PositionForHand(CMajor, Std, 7, 4, 24).K);
            // In E minor (G major) position 1 sits at the open position.
            var eMinor = FretNumbers.Frame(new SongKey(4, minor: true));
            var open = FretNumbers.PositionForHand(eMinor, Std, 1, 4, 24);
            Assert.AreEqual(1, open.K);
            Assert.AreEqual(0, open.Lo);
        }

        [Test]
        public void ListsEveryInKeyNoteBetweenTwoFrets()
        {
            // Fret 5 on each string: A D G C E A -- all in C major.
            Assert.AreEqual(new[] { 0, 1, 2, 3, 4, 5 }, FretNumbers.KeyNotes(CMajor, Std, 5, 5).Select(n => n.S).ToArray());
            // Fret 6 holds the in-key F on the B string and nothing else.
            Assert.AreEqual(new[] { new[] { 4, 6 } },
                            FretNumbers.KeyNotes(CMajor, Std, 6, 6).Select(n => new[] { n.S, n.F }).ToArray());
        }

        // ---- the bleed --------------------------------------------------------------

        [Test]
        public void IsFullInsideABoxAndFadesWithDistanceToAFloor()
        {
            var box = new List<(double, double)> { (5, 8) };
            Assert.AreEqual(1.0, FretNumbers.FocusWeight(6, box), 1e-9);
            Assert.AreEqual(0.76, FretNumbers.FocusWeight(10, box), 1e-9);
            Assert.AreEqual(0.2, FretNumbers.FocusWeight(20, box), 1e-9);
            // The nearest of several windows wins.
            Assert.AreEqual(1.0, FretNumbers.FocusWeight(10, new List<(double, double)> { (5, 8), (10, 12) }), 1e-9);
            // The single-window form agrees.
            Assert.AreEqual(0.76, FretNumbers.FocusWeight(10, 5, 8), 1e-9);
        }

        // ---- chords ------------------------------------------------------------------

        [Test]
        public void ReadsRootAndQualityFromTheNotes()
        {
            AssertTriad(FretNumbers.TriadOf(new[] { 57, 60, 64 }), 9, 3, "m");
            AssertTriad(FretNumbers.TriadOf(new[] { 52, 55, 60 }), 0, 4, ""); // C/E
            AssertTriad(FretNumbers.TriadOf(new[] { 40, 47, 52 }), 4, 0, ""); // E5
            Assert.AreEqual(7, FretNumbers.TriadOf(new[] { 55, 59, 62, 65 })?.Root); // G7 reads as its triad
            Assert.AreEqual("", FretNumbers.TriadOf(new[] { 55, 59, 62, 65 })?.Suffix);
            Assert.AreEqual(11, FretNumbers.TriadOf(new[] { 59, 62, 65 })?.Root);
            Assert.AreEqual("°", FretNumbers.TriadOf(new[] { 59, 62, 65 })?.Suffix);
            Assert.IsNull(FretNumbers.TriadOf(new[] { 60, 72 }));
        }

        static void AssertTriad(Triad? triad, int root, int third, string suffix)
        {
            Assert.IsTrue(triad.HasValue);
            Assert.AreEqual(root, triad.Value.Root);
            Assert.AreEqual(third, triad.Value.Third);
            Assert.AreEqual(suffix, triad.Value.Suffix);
        }

        [Test]
        public void NumbersAChordInTheKey()
        {
            Assert.AreEqual("4", FretNumbers.ChordNumber(CMajor, FretNumbers.TriadOf(new[] { 53, 57, 60 }).Value));
            Assert.AreEqual("6m", FretNumbers.ChordNumber(CMajor, FretNumbers.TriadOf(new[] { 57, 60, 64 }).Value));
            Assert.AreEqual("♭3", FretNumbers.ChordNumber(AMinor, FretNumbers.TriadOf(new[] { 48, 52, 55 }).Value));
            Assert.AreEqual("♭7", FretNumbers.ChordNumber(AMinor, FretNumbers.TriadOf(new[] { 55, 59, 62 }).Value));
            Assert.AreEqual("1m", FretNumbers.ChordNumber(AMinor, FretNumbers.TriadOf(new[] { 57, 60, 64 }).Value));
        }

        [Test]
        public void NamesEachNotesJobInTheChordWithItsRingColour()
        {
            var am = FretNumbers.TriadOf(new[] { 57, 60, 64 }).Value;
            Assert.AreEqual(new[] { "R", "♭3", "5", null }, new[] { 57, 60, 64, 67 }.Select(m => FretNumbers.ChordTone(am, m)).ToArray());
            Assert.AreEqual("3", FretNumbers.ChordTone(FretNumbers.TriadOf(new[] { 48, 52, 55 }).Value, 52));
            Assert.AreEqual(new uint[] { 0x22d3ee, 0xff7aa2, 0xff7aa2, 0xa78bfa },
                            new[] { "R", "3", "♭3", "5" }.Select(FretNumbers.ToneColor).ToArray());
        }

        // ---- looks (guitarViews.ts pitchLook / look) ---------------------------------

        [Test]
        public void LooksLikeTheDesktopsDotsInNumbersAndFrets()
        {
            // The 1: its number, a white ring, the octave's colour.
            var root = FretLooks.PitchLook(CMajor, 40, 48, null, ChordLabelMode.Numbers);
            Assert.AreEqual("1", root.Label);
            Assert.IsTrue(root.WhiteRing);
            Assert.AreEqual(FretNumbers.OctaveColors[1], root.Colour);
            // A scale-completer is hollow, a note outside the key dashed.
            Assert.AreEqual(DotShape.Hollow, FretLooks.PitchLook(CMajor, 40, 53, null, ChordLabelMode.Numbers).Shape);
            Assert.AreEqual(DotShape.Dashed, FretLooks.PitchLook(CMajor, 40, 51, null, ChordLabelMode.Numbers).Shape);

            // In a chord, tones mode names the note's job; both mode rings it.
            var am = FretNumbers.TriadOf(new[] { 57, 60, 64 });
            var tone = FretLooks.PitchLook(CMajor, 40, 60, am, ChordLabelMode.Tones);
            Assert.AreEqual("♭3", tone.Label);
            Assert.AreEqual(FretNumbers.ToneThirdColor, tone.Colour);
            var both = FretLooks.PitchLook(CMajor, 40, 60, am, ChordLabelMode.Both);
            Assert.AreEqual("1", both.Label);
            Assert.AreEqual(FretNumbers.ToneThirdColor, both.ToneRing);

            // Frets: the string's colour, and an open string hollow; a muted note never numbered.
            var open = FretLooks.ForNote(null, 40, 1, 0, 45, false, null, ChordLabelMode.Numbers, "0");
            Assert.AreEqual(DotShape.Hollow, open.Shape);
            Assert.AreEqual(FretLooks.StringColour(1), open.Colour);
            var muted = FretLooks.ForNote(CMajor, 40, 2, 5, 55, true, null, ChordLabelMode.Numbers, "5");
            Assert.AreEqual("5", muted.Label);
            Assert.AreEqual(FretLooks.StringColour(2), muted.Colour);

            // A neck dot keeps its tier and colour but shows the fret when labels are frets.
            var neck = FretLooks.ForNeck(CMajor, false, 40, 8, 48, "8");
            Assert.AreEqual("8", neck.Label);
            Assert.IsTrue(neck.WhiteRing);
        }
    }
}
