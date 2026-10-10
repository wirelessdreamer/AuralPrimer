// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The guitar neck's geometry and its alignment check.
//
// A fret drawn a few millimetres off, or a neck rolled the wrong way, looks
// plausible in the headset and teaches the wrong place on the instrument --
// so the maths is pinned here rather than judged by eye.

using System;
using System.Numerics;
using AuralPrimer.Calibration;
using NUnit.Framework;

namespace AuralPrimer.Link.Tests
{
    public class FretboardTests
    {
        static readonly FretboardGeometry Neck = FretboardGeometry.JamstikStudio;

        static void AssertNear(Vector3 expected, Vector3 actual, float tolerance, string what)
        {
            Assert.That(Vector3.Distance(expected, actual), Is.LessThan(tolerance),
                        $"{what}: expected {expected}, got {actual}");
        }

        static Vector3 Axis(Quaternion q, Vector3 axis) => Vector3.Transform(axis, q);

        // ---- Frets and strings -------------------------------------------

        [Test]
        public void PlacesFretsByEqualTemperament()
        {
            Assert.AreEqual(0f, Neck.FretX(0), 1e-6f);
            Assert.AreEqual(0.648f * (1f - MathF.Pow(2f, -1f / 12f)), Neck.FretX(1), 1e-6f);
            // The 12th fret halves the string and the 24th quarters it.
            Assert.AreEqual(0.324f, Neck.FretX(12), 1e-5f);
            Assert.AreEqual(0.486f, Neck.FretX(24), 1e-5f);
            Assert.AreEqual(0.324f, Neck.TwelfthX, 1e-5f);
        }

        [Test]
        public void StopsTheStringBetweenTheTwoWires()
        {
            Assert.AreEqual(0f, Neck.FretCentreX(0), 1e-6f);
            Assert.AreEqual(0.5f * (Neck.FretX(4) + Neck.FretX(5)), Neck.FretCentreX(5), 1e-6f);
        }

        [Test]
        public void FansTheStringsFromNutToBridgeWithTheLowestOnMinusZ()
        {
            Assert.AreEqual(-0.017f, Neck.StringZ(0, 0f), 1e-6f);
            Assert.AreEqual(0.017f, Neck.StringZ(5, 0f), 1e-6f);
            Assert.AreEqual(-0.026f, Neck.StringZ(0, 0.648f), 1e-6f);
            // Halfway along, halfway between the two spreads.
            Assert.AreEqual(0.043f, Neck.SpreadAt(0.324f), 1e-6f);
            Assert.AreEqual(-0.0215f + 0.043f / 5f, Neck.StringZ(1, 0.324f), 1e-6f);
        }

        [Test]
        public void ABassIsFourStringsOnItsOwnScale()
        {
            var bass = FretboardGeometry.FourStringBass;
            Assert.AreEqual(4, bass.StringCount);
            Assert.AreEqual(0.432f, bass.TwelfthX, 1e-5f);
            Assert.AreEqual(-bass.StringZ(3, 0f), bass.StringZ(0, 0f), 1e-6f);
        }

        [Test]
        public void LeansTheTimeAxisTowardThePlayerFromTheFretboardFace()
        {
            AssertNear(Vector3.UnitY, FretboardGeometry.TimeAxis(0f, -1), 1e-6f, "untilted");
            var leaned = FretboardGeometry.TimeAxis(30f, -1);
            AssertNear(new Vector3(0f, MathF.Cos(MathF.PI / 6f), -0.5f), leaned, 1e-5f, "toward -Z");
            Assert.That(FretboardGeometry.TimeAxis(30f, +1).Z, Is.GreaterThan(0f));
            // Never flat along the strings' own spread.
            Assert.That(FretboardGeometry.TimeAxis(120f, -1).Y, Is.GreaterThan(0.2f));
        }

        // ---- The mount's first guess --------------------------------------

        [Test]
        public void ReadsTheMountFileTheCadWrites()
        {
            // hardware/quest-guitar-mount/mount_offset.json, verbatim.
            const string file = @"{
  ""controller"": ""meta-quest-touch-plus"",
  ""pose"": ""openxr-grip"",
  ""neck_origin_in_grip_m"": [-0.006, -0.0462, -0.1098],
  ""neck_rotation_in_grip_quat"": [-0.49851, 0.6841, 0.02222, 0.53198],
  ""note"": ""First guess from the CAD (approximate grip-pose model); the headset's alignment check replaces it."",
  ""face_to_eyes_deg"": 56.9,
  ""params"": { ""nut_to_tip"": 40.0, ""grip_rise"": 30.0, ""grip_tilt"": 30.0, ""face_roll"": -45.0, ""face_cant"": 50.0 }
}";
            Assert.IsTrue(MountOffset.TryParse(file, out var parsed));
            AssertNear(MountOffset.Cad.Position, parsed.Position, 1e-6f, "origin");
            AssertNear(Axis(MountOffset.Cad.Rotation, Vector3.UnitX), Axis(parsed.Rotation, Vector3.UnitX), 1e-5f, "neck axis");
        }

        [Test]
        public void RefusesAMountFileItCannotRead()
        {
            Assert.IsFalse(MountOffset.TryParse("not json", out _));
            Assert.IsFalse(MountOffset.TryParse("{\"neck_origin_in_grip_m\":[0,0]}", out _));
            Assert.IsFalse(MountOffset.TryParse(
                "{\"neck_origin_in_grip_m\":[0,0,0],\"neck_rotation_in_grip_quat\":[0,0,0,0]}", out _));
        }

        [Test]
        public void TheCadPutsTheNeckBehindTheControllersHead()
        {
            // The cradle points the controller's head away from the guitar, and
            // the grip pose's forward runs up toward that head -- so the neck,
            // running toward the body, points back along grip -Z, with the nut
            // a dozen centimetres behind the grip. The mount raises and tips the
            // controller (30 degrees each) so its top surface leans toward the
            // player, which puts the neck about 41 degrees off grip -Z. Read as
            // the inverse rotation, the same numbers would point it elsewhere.
            var neckAxis = Axis(MountOffset.Cad.Rotation, Vector3.UnitX);
            var dot = Vector3.Dot(neckAxis, -Vector3.UnitZ);
            Assert.That(dot, Is.InRange(0.70f, 0.80f), $"neck axis {neckAxis}");
            Assert.That(MountOffset.Cad.Position.Z, Is.LessThan(-0.1f));
        }

        // ---- The alignment check -----------------------------------------

        /// <summary>The three touches a real neck at <paramref name="truth"/> would give.</summary>
        static (Vector3 low, Vector3 high, Vector3 twelfth) Touches(MountOffset truth)
        {
            Vector3 At(Vector3 local) => truth.Position + Vector3.Transform(local, truth.Rotation);
            return (At(new Vector3(0f, 0f, -0.017f)),
                    At(new Vector3(0f, 0f, 0.017f)),
                    At(new Vector3(Neck.TwelfthX, 0f, 0f)));
        }

        [Test]
        public void ThreeTouchesRecoverTheNeckExactly()
        {
            var truth = FretboardSolver.Nudge(MountOffset.Cad, new Vector3(0.004f, -0.002f, 0.003f), 7f);
            var (low, high, twelfth) = Touches(truth);

            var result = FretboardSolver.SolveThree(Neck, low, high, twelfth);

            Assert.IsTrue(result.RollMeasured);
            Assert.IsTrue(result.Plausible);
            Assert.AreEqual(0.324f, result.MeasuredMetres, 1e-5f);
            AssertNear(truth.Position, result.Offset.Position, 1e-5f, "origin");
            foreach (var axis in new[] { Vector3.UnitX, Vector3.UnitY, Vector3.UnitZ })
            {
                AssertNear(Axis(truth.Rotation, axis), Axis(result.Offset.Rotation, axis), 1e-4f, $"axis {axis}");
            }
        }

        [Test]
        public void ATwelfthTouchFarFromHalfTheScaleIsDoubted()
        {
            var (low, high, _) = Touches(MountOffset.Cad);
            var nut = (low + high) * 0.5f;
            var along = Axis(MountOffset.Cad.Rotation, Vector3.UnitX);

            // 15% short: the player touched the 10th fret, not the 12th.
            var result = FretboardSolver.SolveThree(Neck, low, high, nut + along * (0.324f * 0.85f));
            Assert.IsFalse(result.Plausible);
            Assert.AreEqual(0.15f, result.ErrorRatio, 1e-3f);

            // 5% is within what hand tracking misses by.
            Assert.IsTrue(FretboardSolver.SolveThree(Neck, low, high, nut + along * (0.324f * 1.05f)).Plausible);
        }

        [Test]
        public void TwoTouchesKeepTheMountsRoll()
        {
            // The real neck is the CAD's, swung 6 degrees about an axis across
            // the neck and shifted -- the mount sitting a little off. Two touches
            // cannot see roll, so the answer must take it from the CAD: and since
            // the true error had no roll in it, that answer is exact.
            var prior = MountOffset.Cad;
            var nutLowLocal = new Vector3(0f, 0f, -0.017f);
            var twelfthLocal = new Vector3(Neck.TwelfthX, 0f, 0f);
            var line = Vector3.Normalize(Vector3.Transform(twelfthLocal - nutLowLocal, prior.Rotation));
            var across = Vector3.Normalize(Vector3.Cross(line, Vector3.UnitY));
            var swing = Quaternion.CreateFromAxisAngle(across, 6f * MathF.PI / 180f);
            var truth = new MountOffset(prior.Position + new Vector3(0.003f, 0.001f, -0.002f), swing * prior.Rotation);

            Vector3 At(Vector3 local) => truth.Position + Vector3.Transform(local, truth.Rotation);
            var result = FretboardSolver.SolveTwo(Neck, prior,
                                                  nutLowLocal, At(nutLowLocal),
                                                  twelfthLocal, At(twelfthLocal));

            Assert.IsFalse(result.RollMeasured);
            Assert.IsTrue(result.Plausible);
            AssertNear(truth.Position, result.Offset.Position, 1e-5f, "origin");
            AssertNear(Axis(truth.Rotation, Vector3.UnitY), Axis(result.Offset.Rotation, Vector3.UnitY), 1e-4f, "face");
        }

        [Test]
        public void NudgesMoveAndRollInTheNecksOwnFrame()
        {
            var start = MountOffset.Cad;
            var moved = FretboardSolver.Nudge(start, new Vector3(0.001f, 0f, 0f), 0f);
            AssertNear(start.Position + 0.001f * Axis(start.Rotation, Vector3.UnitX), moved.Position, 1e-6f,
                       "toward the bridge");

            // Positive roll turns the face toward the highest string.
            var rolled = FretboardSolver.Nudge(start, Vector3.Zero, 90f);
            AssertNear(Axis(start.Rotation, Vector3.UnitZ), Axis(rolled.Rotation, Vector3.UnitY), 1e-5f, "face");
            AssertNear(start.Position, rolled.Position, 1e-7f, "rolls about the nut");
        }

        [Test]
        public void ConvertsBetweenTheRoomAndTheGrip()
        {
            var gripPosition = new Vector3(0.2f, 1.1f, -0.4f);
            var gripRotation = Quaternion.Normalize(new Quaternion(0.1f, -0.7f, 0.2f, 0.65f));
            var (neckPosition, _) = FretboardSolver.NeckPose(gripPosition, gripRotation, MountOffset.Cad);

            // The nut, seen from the grip, is where the offset says it is.
            AssertNear(MountOffset.Cad.Position, FretboardSolver.ToGrip(gripPosition, gripRotation, neckPosition),
                       1e-5f, "round trip");
        }

        [Test]
        public void BuildsRotationsFromAxesAndBetweenDirections()
        {
            var x = Vector3.Normalize(new Vector3(0.3f, -0.2f, 0.9f));
            var z = Vector3.Normalize(Vector3.Cross(x, new Vector3(0.1f, 1f, 0.2f)));
            var y = Vector3.Cross(z, x);
            var q = FretboardSolver.FromBasis(x, y, z);
            AssertNear(x, Axis(q, Vector3.UnitX), 1e-5f, "X");
            AssertNear(y, Axis(q, Vector3.UnitY), 1e-5f, "Y");
            AssertNear(z, Axis(q, Vector3.UnitZ), 1e-5f, "Z");

            var from = new Vector3(1f, 0.2f, -0.3f);
            var to = new Vector3(-0.4f, 0.9f, 0.1f);
            AssertNear(Vector3.Normalize(to), Vector3.Transform(Vector3.Normalize(from), FretboardSolver.FromTo(from, to)),
                       1e-5f, "from-to");
            AssertNear(-Vector3.UnitX, Vector3.Transform(Vector3.UnitX, FretboardSolver.FromTo(Vector3.UnitX, -Vector3.UnitX)),
                       1e-5f, "opposite");
        }

        // ---- Losing the controller ---------------------------------------

        [Test]
        public void HoldsALostPoseBrieflyThenFades()
        {
            Assert.AreEqual(1f, TrackingHold.Visibility(0.2f, 0.5f, 1f), 1e-6f);
            Assert.AreEqual(0.5f, TrackingHold.Visibility(1.0f, 0.5f, 1f), 1e-6f);
            Assert.AreEqual(0f, TrackingHold.Visibility(3.0f, 0.5f, 1f), 1e-6f);
        }
    }
}
