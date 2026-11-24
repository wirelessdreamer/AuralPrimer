// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The kit's calibration: a pad unit's grid from its corners, pieces carried
// between spaces, and a kit-only profile surviving a restart.
//
// Each of these fails silently in the headset -- a cue a pad over, a kit that
// comes back across the room, a kit that never comes back -- so they are
// pinned here rather than found by playing.

using System.IO;
using AuralPrimer.Calibration;
using NUnit.Framework;
using UnityEngine;

namespace AuralPrimer.Link.Tests
{
    public class KitCalibrationTests
    {
        static readonly string[] Ids =
        {
            "crash", "ride", "hihat_open",
            "tom_high", "snare", "hihat_closed",
            "tom_mid", "kick", "tom_low",
        };

        // A 36 x 30 cm pad face flat on a table, the player standing at -z.
        static readonly Vector3 FarLeft = new(0f, 0.8f, 0.4f);
        static readonly Vector3 FarRight = new(0.36f, 0.8f, 0.4f);
        static readonly Vector3 NearLeft = new(0f, 0.8f, 0.1f);
        static readonly Vector3 Head = new(0.18f, 1.4f, -0.3f);

        static readonly float[] EqualRows = { 1f, 1f, 1f };

        static void AssertNear(Vector3 expected, Vector3 actual, string what)
        {
            Assert.That(Vector3.Distance(expected, actual), Is.LessThan(1e-4f),
                        $"{what}: expected {expected}, got {actual}");
        }

        // ---- The grid ----------------------------------------------------

        [Test]
        public void AcceptsAPadUnitSeenFromTheFront()
        {
            Assert.AreEqual(PadGrid.Problem.None, PadGrid.Check(FarLeft, FarRight, NearLeft, Head - FarLeft));
        }

        [Test]
        public void PutsEachPadInTheMiddleOfItsCell()
        {
            var pieces = PadGrid.Build(FarLeft, FarRight, NearLeft, Ids, EqualRows);

            Assert.AreEqual(9, pieces.Count);
            AssertNear(new Vector3(0.06f, 0.8f, 0.35f), pieces[0].centre, "far-left");
            AssertNear(new Vector3(0.18f, 0.8f, 0.25f), pieces[4].centre, "centre");
            AssertNear(new Vector3(0.18f, 0.8f, 0.15f), pieces[7].centre, "near-middle");
            Assert.AreEqual("crash", pieces[0].id);
            Assert.AreEqual("snare", pieces[4].id);
            Assert.AreEqual("kick", pieces[7].id);
        }

        [Test]
        public void FacesThePadsUpAndKeepsThemInsideTheirCells()
        {
            var pieces = PadGrid.Build(FarLeft, FarRight, NearLeft, Ids, EqualRows);
            foreach (var piece in pieces)
            {
                AssertNear(Vector3.up, piece.normal, $"{piece.id} normal");
                // Cells are 12 x 10 cm; a cue wider than the narrow side would
                // spill onto the next pad.
                Assert.That(piece.RadiusMetres, Is.GreaterThan(0.02f).And.LessThan(0.05f));
                Assert.AreEqual(-1, piece.midiNote, "notes are learned by striking");
            }
        }

        [Test]
        public void FollowsAUnitTiltedOnItsStand()
        {
            // Raked 30 degrees toward the player about its own left-right axis.
            var tilt = Quaternion.AngleAxis(-30f, Vector3.right);
            Vector3 T(Vector3 p) => FarLeft + tilt * (p - FarLeft);

            Assert.AreEqual(PadGrid.Problem.None,
                            PadGrid.Check(T(FarLeft), T(FarRight), T(NearLeft), Head - FarLeft));

            var pieces = PadGrid.Build(T(FarLeft), T(FarRight), T(NearLeft), Ids, EqualRows);
            AssertNear(T(new Vector3(0.18f, 0.8f, 0.25f)), pieces[4].centre, "centre");
            AssertNear(tilt * Vector3.up, pieces[4].normal, "normal");
        }

        [Test]
        public void RefusesLeftAndRightSwapped()
        {
            // Pinching the right corner first would put the crash on the right.
            Assert.AreEqual(PadGrid.Problem.Mirrored,
                            PadGrid.Check(FarRight, FarLeft, new Vector3(0.36f, 0.8f, 0.1f), Head - FarRight));
        }

        [Test]
        public void SaysSoWhenOnlyTheFirstTwoCornersAreSwapped()
        {
            // Far-right, far-left, then the right near corner: square from the
            // second corner, not the first, which is a swap rather than a skew.
            Assert.AreEqual(PadGrid.Problem.Mirrored,
                            PadGrid.Check(FarRight, FarLeft, NearLeft, Head - FarRight));
        }

        [Test]
        public void CallsTheNearRightCornerSkewedNotSwapped()
        {
            // Left and right were right; the third pinch went to the wrong side.
            Assert.AreEqual(PadGrid.Problem.Skewed,
                            PadGrid.Check(FarLeft, FarRight, new Vector3(0.36f, 0.8f, 0.1f), Head - FarLeft));
        }

        [Test]
        public void KeepsEdgeTriggersOnAShallowPadFace()
        {
            // 16 cm deep: the edge strip's own cue would come out under 1 cm, and
            // a pad that small fails IsComplete and would be dropped unseen.
            var pieces = PadGrid.Build(FarLeft, FarRight, new Vector3(0f, 0.8f, 0.24f), Ids, PadGrid.SpdsRows);
            Assert.That(pieces[0].RadiusMetres, Is.GreaterThan(0.01f));
        }

        [Test]
        public void KeepsTheSpdsEdgeTriggersOnTheirThinStrip()
        {
            var pieces = PadGrid.Build(FarLeft, FarRight, NearLeft, Ids, PadGrid.SpdsRows);

            // 30 cm deep: a 4.5 cm strip of edge triggers, then two 12.75 cm rows.
            AssertNear(new Vector3(0.06f, 0.8f, 0.4f - 0.0225f), pieces[0].centre, "left edge trigger");
            AssertNear(new Vector3(0.18f, 0.8f, 0.4f - 0.045f - 0.06375f), pieces[4].centre, "back-middle pad");
            AssertNear(new Vector3(0.18f, 0.8f, 0.4f - 0.045f - 0.19125f), pieces[7].centre, "front-middle pad");
            // A cue no deeper than the strip it sits on.
            Assert.That(pieces[0].RadiusMetres, Is.LessThanOrEqualTo(0.0225f));
            Assert.That(pieces[4].RadiusMetres, Is.GreaterThan(pieces[0].RadiusMetres));
        }

        [Test]
        public void RefusesCornersThatAreNotAPadUnit()
        {
            var toward = Head - FarLeft;
            Assert.AreEqual(PadGrid.Problem.TooSmall,
                            PadGrid.Check(FarLeft, FarLeft + new Vector3(0.05f, 0f, 0f), NearLeft, toward));
            Assert.AreEqual(PadGrid.Problem.TooLarge,
                            PadGrid.Check(FarLeft, FarLeft + new Vector3(1.2f, 0f, 0f), NearLeft, toward));
            // A third corner 45 degrees along the far edge, not straight toward the player.
            Assert.AreEqual(PadGrid.Problem.Skewed,
                            PadGrid.Check(FarLeft, FarRight, FarLeft + new Vector3(0.2f, 0f, -0.2f), toward));
        }

        // ---- Spaces ------------------------------------------------------

        [Test]
        public void CarriesAPieceIntoAnAnchorAndBack()
        {
            var anchor = new GameObject("anchor").transform;
            try
            {
                anchor.SetPositionAndRotation(new Vector3(1f, 0.5f, -2f), Quaternion.Euler(10f, 70f, -5f));
                var piece = new KitPiece
                {
                    id = "snare",
                    centre = new Vector3(0.2f, 0.7f, 0.4f),
                    rim = new Vector3(0.32f, 0.7f, 0.4f),
                    normal = new Vector3(0f, 0.8f, -0.6f),
                };

                piece.Rebase(null, anchor);
                AssertNear(new Vector3(0.2f, 0.7f, 0.4f), anchor.TransformPoint(piece.centre), "centre in the room");
                Assert.AreEqual(0.12f, piece.RadiusMetres, 1e-4f, "an anchor does not resize a pad");

                piece.Rebase(anchor, null);
                AssertNear(new Vector3(0.2f, 0.7f, 0.4f), piece.centre, "centre");
                AssertNear(new Vector3(0.32f, 0.7f, 0.4f), piece.rim, "rim");
                AssertNear(new Vector3(0f, 0.8f, -0.6f), piece.normal, "normal");
            }
            finally
            {
                Object.DestroyImmediate(anchor.gameObject);
            }
        }

        // ---- Persistence -------------------------------------------------

        [Test]
        public void KeepsAProfileThatHasOnlyAKit()
        {
            const string name = "kit-calibration-test";
            try
            {
                var profile = new CalibrationProfile
                {
                    profileName = name,
                    instrument = Instrument.Drums,
                    kitAnchorId = "00000000-0000-0000-0000-000000000001",
                    kitIsPadGrid = true,
                };
                profile.kitPieces.Add(new KitPiece
                {
                    id = "snare",
                    midiNote = 64,
                    centre = Vector3.zero,
                    rim = new Vector3(0.04f, 0f, 0f),
                });
                profile.Save();

                var loaded = CalibrationProfile.Load(name);

                Assert.IsNotNull(loaded, "a kit with no keyboard edges was thrown away");
                Assert.AreEqual(Instrument.Drums, loaded.instrument);
                Assert.IsTrue(loaded.IsKitCalibrated);
                Assert.IsTrue(loaded.kitIsPadGrid);
                Assert.AreEqual("00000000-0000-0000-0000-000000000001", loaded.kitAnchorId);
                Assert.AreEqual(64, loaded.PieceForLane("snare").midiNote);
            }
            finally
            {
                File.Delete(CalibrationProfile.PathFor(name));
            }
        }

        [Test]
        public void StillDiscardsAProfileWithNothingInIt()
        {
            const string name = "kit-calibration-empty-test";
            try
            {
                new CalibrationProfile { profileName = name }.Save();
                Assert.IsNull(CalibrationProfile.Load(name));
            }
            finally
            {
                File.Delete(CalibrationProfile.PathFor(name));
            }
        }
    }
}
