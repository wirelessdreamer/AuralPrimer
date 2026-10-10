// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// How one note looks on a guitar view: its label, colour and shape, and the
// rings around it. The port of guitarViews.ts's look / pitchLook / neckLook,
// so a dot reads the same on the headset as on the desktop.
//
// Free of UnityEngine; colours are 0xRRGGBB and the views convert them.

namespace AuralPrimer.Calibration
{
    /// <summary>Label notes by fret, or by Nashville number from the song's key.</summary>
    public enum FretLabelMode
    {
        Frets = 0,
        Numbers = 1,
    }

    /// <summary>What a chord's notes show: their number, their job in the chord, or both.</summary>
    public enum ChordLabelMode
    {
        Numbers = 0,
        Tones = 1,
        Both = 2,
    }

    /// <summary>Filled; hollow (an open string, or a note that completes the scale); dashed (outside the key).</summary>
    public enum DotShape
    {
        Fill,
        Hollow,
        Dashed,
    }

    public readonly struct NoteLook
    {
        public readonly string Label;
        public readonly uint Colour;
        public readonly DotShape Shape;
        /// <summary>A white ring: the 1.</summary>
        public readonly bool WhiteRing;
        /// <summary>A chord-tone ring colour, or 0 for none.</summary>
        public readonly uint ToneRing;

        public NoteLook(string label, uint colour, DotShape shape, bool whiteRing = false, uint toneRing = 0)
        {
            Label = label;
            Colour = colour;
            Shape = shape;
            WhiteRing = whiteRing;
            ToneRing = toneRing;
        }

        public NoteLook WithLabel(string label) => new(label, Colour, Shape, WhiteRing, ToneRing);
        public NoteLook WithShape(DotShape shape) => new(Label, Colour, shape, WhiteRing, ToneRing);
    }

    public static class FretLooks
    {
        /// <summary>
        /// String colours, lowest string first: the red / yellow / blue / orange /
        /// green / purple order tab-highway players know, then two more for seven-
        /// and eight-string guitars.
        /// </summary>
        public static readonly uint[] StringColours =
        {
            0xff5c7a, 0xf5d547, 0x38bdf8, 0xfb923c, 0x34d399, 0xa78bfa, 0xf472b6, 0xa3e635,
        };

        public static uint StringColour(int s) =>
            StringColours[((s % StringColours.Length) + StringColours.Length) % StringColours.Length];

        /// <summary>Note names as the desktop's tab spells them (viz-tab fretted.ts).</summary>
        public static readonly string[] NoteNames = { "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B" };

        /// <summary>A string's name from its open pitch: the highest string lower-case when it shares the lowest's name (E A D G B e).</summary>
        public static string StringLabel(System.Collections.Generic.IReadOnlyList<int> open, int s)
        {
            var name = NoteNames[((open[s] % 12) + 12) % 12];
            var lowest = NoteNames[((open[0] % 12) + 12) % 12];
            return s == open.Count - 1 && s > 0 && name == lowest ? name.ToLowerInvariant() : name;
        }

        /// <summary>
        /// How a pitch looks in numbers: its number (or its chord tone), its
        /// octave's colour, its tier's shape, a white ring on the 1.
        /// </summary>
        public static NoteLook PitchLook(NumberFrame frame, int lowestOpen, int midi, Triad? triad, ChordLabelMode chords)
        {
            var tone = triad.HasValue ? FretNumbers.ChordTone(triad.Value, midi) : null;
            if (tone != null && chords == ChordLabelMode.Tones)
            {
                return new NoteLook(tone, FretNumbers.ToneColor(tone), DotShape.Fill);
            }
            var tier = FretNumbers.TierOf(frame, midi);
            return new NoteLook(
                FretNumbers.DegreeLabel(frame, midi),
                FretNumbers.OctaveColors[FretNumbers.OctaveBand(frame, lowestOpen, midi)],
                tier == NumberTier.Mode ? DotShape.Hollow : tier == NumberTier.Chrom ? DotShape.Dashed : DotShape.Fill,
                tier == NumberTier.Root,
                tone != null && chords == ChordLabelMode.Both ? FretNumbers.ToneColor(tone) : 0);
        }

        /// <summary>
        /// How a chart note looks: by fret and string, or by number when
        /// <paramref name="numbers"/> is set. A muted note has no pitch to number.
        /// </summary>
        public static NoteLook ForNote(NumberFrame? numbers, int lowestOpen, int s, int fret, int midi,
                                       bool muted, Triad? triad, ChordLabelMode chords, string fretLabel)
        {
            if (!numbers.HasValue || muted)
            {
                // Open strings hollow.
                var shape = fret == 0 && !muted ? DotShape.Hollow : DotShape.Fill;
                return new NoteLook(fretLabel, StringColour(s), shape);
            }
            return PitchLook(numbers.Value, lowestOpen, midi, triad, chords);
        }

        /// <summary>How a neck dot looks: numbered, or its fret when labels are frets.</summary>
        public static NoteLook ForNeck(NumberFrame frame, bool numbersOn, int lowestOpen, int fret, int midi, string fretLabel)
        {
            var look = PitchLook(frame, lowestOpen, midi, null, ChordLabelMode.Numbers);
            return numbersOn ? look : look.WithLabel(fretLabel);
        }
    }
}
