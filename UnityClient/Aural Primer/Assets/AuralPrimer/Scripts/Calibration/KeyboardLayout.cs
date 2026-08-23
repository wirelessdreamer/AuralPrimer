// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Where a MIDI pitch sits along a keyboard, as a fraction of its playable
// width. The same white/black placement the 2D client uses, generalised from
// its hard-coded 88 keys to whatever range the player actually owns.
//
// No UnityEngine dependency: this is the piece that must agree exactly with the
// desktop renderer, so it has to be testable on its own.

using System;

namespace AuralPrimer.Calibration
{
    /// <summary>Geometry of one physical keyboard, derived from its MIDI range.</summary>
    public sealed class KeyboardLayout
    {
        /// <summary>Real white keys are ~23.5 mm wide (an octave spans ~165 mm).
        /// Used to sanity-check a calibration against the instrument's true size.</summary>
        public const double WhiteKeyWidthMetres = 0.0235;

        static readonly bool[] BlackPitchClass =
        {
            false, true, false, true, false, false, true, false, true, false, true, false
        };

        public int LowestPitch { get; }
        public int HighestPitch { get; }
        public int WhiteKeyCount { get; }

        public KeyboardLayout(int lowestPitch, int highestPitch)
        {
            if (highestPitch <= lowestPitch)
            {
                throw new ArgumentException(
                    $"highest ({highestPitch}) must be above lowest ({lowestPitch})");
            }

            // A keyboard starts and ends on a white key on every instrument you
            // can buy. Snapping outward rather than rejecting means a player who
            // taps a black key by accident still gets a usable calibration.
            LowestPitch = SnapDownToWhite(lowestPitch);
            HighestPitch = SnapUpToWhite(highestPitch);
            WhiteKeyCount = CountWhiteKeys(LowestPitch, HighestPitch);
        }

        public static bool IsBlack(int pitch) => BlackPitchClass[Mod(pitch, 12)];

        public int KeyCount => HighestPitch - LowestPitch + 1;

        /// <summary>Expected physical width in metres, for cross-checking a
        /// measured span. A calibration far off this is a mis-tap, not a
        /// strangely-sized piano.</summary>
        public double ExpectedWidthMetres => WhiteKeyCount * WhiteKeyWidthMetres;

        /// <summary>
        /// Centre of a key as a fraction of the keyboard's width, 0 at the left
        /// edge of the lowest key and 1 at the right edge of the highest.
        /// </summary>
        /// <remarks>
        /// White keys tile evenly. A black key straddles the boundary between the
        /// two white keys either side of it, which is what puts it visually
        /// between them rather than centred on a slot of its own — the same rule
        /// the 2D piano roll uses, so the two clients cannot disagree about where
        /// a C sharp is.
        /// </remarks>
        public double NormalisedX(int pitch)
        {
            if (WhiteKeyCount <= 0) return 0.0;

            var whiteWidth = 1.0 / WhiteKeyCount;

            if (!IsBlack(pitch))
            {
                var index = CountWhiteKeys(LowestPitch, pitch) - 1;
                return (index + 0.5) * whiteWidth;
            }

            // Black: sits on the boundary above the white key below it.
            var whitesBelow = CountWhiteKeys(LowestPitch, pitch - 1);
            return whitesBelow * whiteWidth;
        }

        /// <summary>Width of a key as a fraction of the keyboard's width.</summary>
        public double NormalisedWidth(int pitch)
        {
            if (WhiteKeyCount <= 0) return 0.0;
            var whiteWidth = 1.0 / WhiteKeyCount;
            return IsBlack(pitch) ? whiteWidth * 0.62 : whiteWidth;
        }

        public bool Contains(int pitch) => pitch >= LowestPitch && pitch <= HighestPitch;

        /// <summary>
        /// How far a measured span departs from the expected physical width, as
        /// a ratio. 0 means exact; 0.2 means 20% out.
        /// </summary>
        public double WidthErrorRatio(double measuredMetres)
        {
            if (ExpectedWidthMetres <= 0) return 0.0;
            return Math.Abs(measuredMetres - ExpectedWidthMetres) / ExpectedWidthMetres;
        }

        /// <summary>Common sizes, for the manual override when MIDI is not
        /// connected. Ranges are the usual ones; detection by playing covers
        /// the instruments that differ.</summary>
        public static KeyboardLayout ForKeyCount(int keys)
        {
            return keys switch
            {
                88 => new KeyboardLayout(21, 108),  // A0-C8
                76 => new KeyboardLayout(28, 103),  // E1-G7
                61 => new KeyboardLayout(36, 96),   // C2-C7
                49 => new KeyboardLayout(36, 84),   // C2-C6
                37 => new KeyboardLayout(48, 84),   // C3-C6
                25 => new KeyboardLayout(48, 72),   // C3-C5
                _ => new KeyboardLayout(36, 96),
            };
        }

        static int CountWhiteKeys(int fromPitch, int toPitch)
        {
            var count = 0;
            for (var p = fromPitch; p <= toPitch; p++)
            {
                if (!IsBlack(p)) count++;
            }
            return count;
        }

        static int SnapDownToWhite(int pitch)
        {
            while (IsBlack(pitch)) pitch--;
            return pitch;
        }

        static int SnapUpToWhite(int pitch)
        {
            while (IsBlack(pitch)) pitch++;
            return pitch;
        }

        static int Mod(int a, int n) => ((a % n) + n) % n;
    }
}
