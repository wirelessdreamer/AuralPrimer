// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Where the frets and strings of a real neck are, in its own frame.
//
// The neck frame (shared with the mount's CAD, hardware/quest-guitar-mount):
//   origin  the nut, centred across the fretboard, on the fretboard surface
//   +X      along the neck toward the bridge
//   +Y      out of the fretboard face, toward the strings
//   +Z      Vector3.Cross(X, Y): from the low-E edge to the high-e edge on a
//           right-handed guitar
//
// Nothing here is measured per fret. Frets follow from the scale length by
// equal temperament, and strings fan out linearly from the nut to the bridge,
// so a nut, a scale length and two spreads place every note on the neck.
//
// Free of UnityEngine (System.Numerics, whose vector maths is the same
// arithmetic as Unity's) so it is tested in a plain runner.

using System;
using System.Numerics;

namespace AuralPrimer.Calibration
{
    public readonly struct FretboardGeometry
    {
        /// <summary>Nut to bridge saddle, metres.</summary>
        public readonly float ScaleLength;
        public readonly int FretCount;
        public readonly int StringCount;
        /// <summary>Lowest to highest string centre at the nut, metres.</summary>
        public readonly float NutSpread;
        /// <summary>Lowest to highest string centre at the bridge, metres.</summary>
        public readonly float BridgeSpread;

        public FretboardGeometry(float scaleLength, int fretCount, int stringCount, float nutSpread, float bridgeSpread)
        {
            ScaleLength = scaleLength;
            FretCount = fretCount;
            StringCount = stringCount;
            NutSpread = nutSpread;
            BridgeSpread = bridgeSpread;
        }

        /// <summary>The Jamstik Studio: 25.5" (648 mm) scale, 24 frets, six strings.</summary>
        public static FretboardGeometry JamstikStudio => new(0.648f, 24, 6, 0.034f, 0.052f);

        /// <summary>A typical four-string bass: 34" scale, 20 frets.</summary>
        /// <remarks>Spreads are common values, not a particular instrument's; the profile adjusts them.</remarks>
        public static FretboardGeometry FourStringBass => new(0.864f, 20, 4, 0.029f, 0.057f);

        /// <summary>The largest share a nut-to-12th measurement may be off before it is doubted.</summary>
        public const float MaxTwelfthError = 0.10f;

        /// <summary>Distance from the nut to fret wire <paramref name="fret"/>: L·(1 − 2^(−f/12)).</summary>
        public float FretX(int fret) =>
            fret <= 0 ? 0f : ScaleLength * (1f - MathF.Pow(2f, -fret / 12f));

        /// <summary>
        /// Where a finger stops the string for <paramref name="fret"/>: midway
        /// between that fret's wire and the one before it. Open is the nut.
        /// </summary>
        public float FretCentreX(int fret) =>
            fret <= 0 ? 0f : 0.5f * (FretX(fret - 1) + FretX(fret));

        /// <summary>The 12th fret, exactly half the scale.</summary>
        public float TwelfthX => FretX(12);

        /// <summary>Lowest-to-highest string spread at <paramref name="x"/> along the neck.</summary>
        public float SpreadAt(float x)
        {
            var t = ScaleLength > 0f ? Math.Clamp(x / ScaleLength, 0f, 1f) : 0f;
            return NutSpread + (BridgeSpread - NutSpread) * t;
        }

        /// <summary>
        /// String <paramref name="s"/> across the neck at <paramref name="x"/>.
        /// The lowest string (0) is on the −Z edge.
        /// </summary>
        public float StringZ(int s, float x)
        {
            if (StringCount <= 1) return 0f;
            var spread = SpreadAt(x);
            return -0.5f * spread + s * spread / (StringCount - 1);
        }

        /// <summary>A stopped note's point on the neck, lifted off the board.</summary>
        public Vector3 NotePoint(int s, int fret, float liftMetres)
        {
            var x = FretCentreX(fret);
            return new Vector3(x, liftMetres, StringZ(s, x));
        }

        /// <summary>
        /// How far a nut-to-12th measurement is from half the scale, as a share
        /// of it: 0.1 is ten percent out.
        /// </summary>
        public float TwelfthErrorRatio(float measuredMetres) =>
            TwelfthX > 0f ? MathF.Abs(measuredMetres - TwelfthX) / TwelfthX : float.PositiveInfinity;

        /// <summary>
        /// The direction notes travel in from, in the neck frame.
        /// </summary>
        /// <remarks>
        /// Hinged at the neck, like the keyboard lane at the keys: straight out
        /// of the fretboard face at a tilt of zero, leaning toward the player's
        /// side of the neck as the tilt grows. Which side that is depends on how
        /// the guitar is held, so the caller says (+1 toward +Z, −1 toward −Z).
        /// A tilt of 90° would lay the lane flat along the strings' own spread,
        /// stacking one string's future on another's present, so it is capped.
        /// </remarks>
        public static Vector3 TimeAxis(float tiltDegrees, int playerSideZ)
        {
            var tilt = Math.Clamp(tiltDegrees, 0f, MaxTiltDegrees) * MathF.PI / 180f;
            var side = playerSideZ >= 0 ? 1f : -1f;
            return Vector3.Normalize(new Vector3(0f, MathF.Cos(tilt), side * MathF.Sin(tilt)));
        }

        /// <summary>Steepest the note lane may lean over the strings.</summary>
        public const float MaxTiltDegrees = 75f;
    }
}
