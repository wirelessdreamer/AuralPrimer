// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The guitar's alignment check: turn a few touched points into the mount
// offset that is actually true for this guitar.
//
// The points are taken in the GRIP frame -- each one converted through the
// mounted controller's pose at the moment it was captured. That is what makes
// the answer survive the player moving: the result is where the neck is
// relative to the controller, which is fixed by the mount, not where it was
// in the room while setup ran.
//
// Free of UnityEngine (System.Numerics) so the maths is tested in a plain
// runner; GuitarAnchor converts at the boundary.

using System;
using System.Numerics;

namespace AuralPrimer.Calibration
{
    public static class FretboardSolver
    {
        public readonly struct Result
        {
            public readonly MountOffset Offset;
            /// <summary>The span the check measured, metres: nut centre to 12th fret.</summary>
            public readonly float MeasuredMetres;
            /// <summary>What that span should be for this neck.</summary>
            public readonly float ExpectedMetres;
            /// <summary>The neck's roll came from the touches, not the mount.</summary>
            public readonly bool RollMeasured;

            public Result(MountOffset offset, float measured, float expected, bool rollMeasured)
            {
                Offset = offset;
                MeasuredMetres = measured;
                ExpectedMetres = expected;
                RollMeasured = rollMeasured;
            }

            /// <summary>Share the measured span is off by: 0.1 is ten percent.</summary>
            public float ErrorRatio =>
                ExpectedMetres > 0f ? MathF.Abs(MeasuredMetres - ExpectedMetres) / ExpectedMetres : float.PositiveInfinity;

            /// <summary>
            /// Close enough to the neck's real size to be the neck. More than
            /// about ten percent off means a touch missed, the way the keyboard's
            /// span check catches a mis-tapped edge.
            /// </summary>
            public bool Plausible => ErrorRatio <= FretboardGeometry.MaxTwelfthError;
        }

        /// <summary>
        /// Three touches: the nut beside the lowest string, the nut beside the
        /// highest, and the centre of the 12th fret.
        /// </summary>
        /// <remarks>
        /// The nut pair fixes the origin (their midpoint) and the roll (the line
        /// between them is the neck's width axis); the 12th fret, 324 mm away on
        /// a 25.5" scale, fixes the neck's direction over a long baseline. The
        /// width axis is only 34 mm long, so hand-tracked touches leave its roll
        /// a few degrees uncertain -- which is what the roll nudge is for.
        /// </remarks>
        public static Result SolveThree(FretboardGeometry neck, Vector3 nutLowest, Vector3 nutHighest, Vector3 twelfth)
        {
            var origin = (nutLowest + nutHighest) * 0.5f;
            var along = twelfth - origin;
            var length = along.Length();
            if (length < 1e-4f) return new Result(new MountOffset(origin, Quaternion.Identity), length, neck.TwelfthX, false);

            var x = along / length;
            var across = nutHighest - nutLowest;
            var z = across - Vector3.Dot(across, x) * x;
            if (z.LengthSquared() < 1e-10f)
            {
                // Both nut touches landed on one spot, or on the neck's own line:
                // no width axis to take a roll from.
                return new Result(new MountOffset(origin, Quaternion.Identity), length, neck.TwelfthX, false);
            }
            z = Vector3.Normalize(z);
            var y = Vector3.Cross(z, x); // with Z = Cross(X, Y), this is the face normal

            return new Result(new MountOffset(origin, FromBasis(x, y, z)), length, neck.TwelfthX, true);
        }

        /// <summary>
        /// Two touches, with the roll kept from <paramref name="prior"/> -- the
        /// CAD's, or a previous check's.
        /// </summary>
        /// <remarks>
        /// <paramref name="expectedA"/> and <paramref name="expectedB"/> are where
        /// the touched points are on the neck, in its own frame. The neck is
        /// turned the least it can be to point the expected line along the
        /// measured one, which leaves its roll about that line as the prior had
        /// it, then placed so both points miss by the same amount.
        /// </remarks>
        public static Result SolveTwo(FretboardGeometry neck, MountOffset prior,
                                      Vector3 expectedA, Vector3 measuredA,
                                      Vector3 expectedB, Vector3 measuredB)
        {
            var expectedLine = Vector3.Transform(expectedB - expectedA, prior.Rotation);
            var measuredLine = measuredB - measuredA;
            var rotation = Quaternion.Normalize(FromTo(expectedLine, measuredLine) * prior.Rotation);

            var origin = 0.5f * (measuredA - Vector3.Transform(expectedA, rotation)
                               + measuredB - Vector3.Transform(expectedB, rotation));

            return new Result(new MountOffset(origin, rotation),
                              measuredLine.Length(), (expectedB - expectedA).Length(), false);
        }

        /// <summary>
        /// Move the neck by a small amount in its own frame, and roll it about
        /// its own length (positive turns the face toward the highest string).
        /// </summary>
        public static MountOffset Nudge(MountOffset offset, Vector3 neckLocalDelta, float rollDegrees)
        {
            var position = offset.Position + Vector3.Transform(neckLocalDelta, offset.Rotation);
            var roll = Quaternion.CreateFromAxisAngle(Vector3.UnitX, rollDegrees * MathF.PI / 180f);
            return new MountOffset(position, offset.Rotation * roll);
        }

        /// <summary>Where the neck is, given where the mounted controller's grip is.</summary>
        public static (Vector3 Position, Quaternion Rotation) NeckPose(
            Vector3 gripPosition, Quaternion gripRotation, MountOffset offset) =>
            (gripPosition + Vector3.Transform(offset.Position, gripRotation),
             Quaternion.Normalize(gripRotation * offset.Rotation));

        /// <summary>A world point in the grip frame.</summary>
        public static Vector3 ToGrip(Vector3 gripPosition, Quaternion gripRotation, Vector3 world) =>
            Vector3.Transform(world - gripPosition, Quaternion.Inverse(gripRotation));

        /// <summary>The rotation whose X, Y and Z axes are the given orthonormal vectors.</summary>
        public static Quaternion FromBasis(Vector3 x, Vector3 y, Vector3 z)
        {
            // Rotation matrix with the axes as columns.
            float m00 = x.X, m01 = y.X, m02 = z.X;
            float m10 = x.Y, m11 = y.Y, m12 = z.Y;
            float m20 = x.Z, m21 = y.Z, m22 = z.Z;

            var trace = m00 + m11 + m22;
            Quaternion q;
            if (trace > 0f)
            {
                var s = MathF.Sqrt(trace + 1f) * 2f;
                q = new Quaternion((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25f * s);
            }
            else if (m00 > m11 && m00 > m22)
            {
                var s = MathF.Sqrt(1f + m00 - m11 - m22) * 2f;
                q = new Quaternion(0.25f * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s);
            }
            else if (m11 > m22)
            {
                var s = MathF.Sqrt(1f + m11 - m00 - m22) * 2f;
                q = new Quaternion((m01 + m10) / s, 0.25f * s, (m12 + m21) / s, (m02 - m20) / s);
            }
            else
            {
                var s = MathF.Sqrt(1f + m22 - m00 - m11) * 2f;
                q = new Quaternion((m02 + m20) / s, (m12 + m21) / s, 0.25f * s, (m10 - m01) / s);
            }
            return Quaternion.Normalize(q);
        }

        /// <summary>The smallest rotation taking direction <paramref name="from"/> to <paramref name="to"/>.</summary>
        public static Quaternion FromTo(Vector3 from, Vector3 to)
        {
            var a = Vector3.Normalize(from);
            var b = Vector3.Normalize(to);
            var dot = Vector3.Dot(a, b);
            if (dot > 0.999999f) return Quaternion.Identity;
            if (dot < -0.999999f)
            {
                // Opposite: any axis at right angles to both will do.
                var axis = Vector3.Cross(Vector3.UnitX, a);
                if (axis.LengthSquared() < 1e-6f) axis = Vector3.Cross(Vector3.UnitY, a);
                return Quaternion.CreateFromAxisAngle(Vector3.Normalize(axis), MathF.PI);
            }
            var c = Vector3.Cross(a, b);
            return Quaternion.Normalize(new Quaternion(c.X, c.Y, c.Z, 1f + dot));
        }
    }
}
