// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Nashville numbers on a fretboard, by pentatonic position: a port of
// visualizers/viz-tab/src/fretNumbers.ts, kept line for line so the headset
// and the desktop give every dot the same number. FretNumbersTests.cs ports
// that file's vitest cases; if either side changes, change both.
//
// The model (docs/guitar-prototype/nashville-fretboard.html):
//
//  - Positions are geometry, numbers come from the key. The five minor-
//    pentatonic boxes are places on the neck (blues/rock numbering: position 1
//    is the minor box with its root on the lowest string); every dot gets its
//    number from the key.
//  - Three tiers: the box's five notes, the two that complete the scale, and
//    notes outside the key (drawn only when the chart plays them).
//  - A position is a focus, not a fence: numbers fade with distance from the
//    focused box to a floor that never reaches zero.
//  - Colour is the octave, starting fresh at each 1 counted up from the lowest
//    string, and any octave of a number counts as that number.
//
// String index 0 is the lowest string, and `open` is the open strings as MIDI,
// lowest first, so bass and other tunings work the same way as standard guitar.
//
// Free of UnityEngine, so it is tested in a plain runner.

using System;
using System.Collections.Generic;

namespace AuralPrimer.Calibration
{
    /// <summary>Count from the song's own 1, or from its relative (major / minor on the same notes).</summary>
    public enum CountFrom
    {
        Song = 0,
        Relative = 1,
    }

    /// <summary>Root: the 1 (ringed). Box: pentatonic. Mode: completes the scale. Chrom: outside the key.</summary>
    public enum NumberTier
    {
        Root,
        Box,
        Mode,
        Chrom,
    }

    /// <summary>The song's key: a tonic pitch class and a mode.</summary>
    public readonly struct SongKey
    {
        public readonly int PitchClass;
        public readonly bool Minor;

        public SongKey(int pitchClass, bool minor)
        {
            PitchClass = pitchClass;
            Minor = minor;
        }
    }

    /// <summary>
    /// A key, as counted. <see cref="Tonic"/> is the 1 and <see cref="Minor"/>
    /// picks the spelling table; <see cref="MajorPc"/> and <see cref="BoxRoot"/>
    /// fix the geometry, which counting never moves.
    /// </summary>
    public readonly struct NumberFrame
    {
        public readonly int Tonic;
        public readonly bool Minor;
        public readonly int MajorPc;
        public readonly int BoxRoot;

        public NumberFrame(int tonic, bool minor, int majorPc, int boxRoot)
        {
            Tonic = tonic;
            Minor = minor;
            MajorPc = majorPc;
            BoxRoot = boxRoot;
        }
    }

    /// <summary>A place on the neck: string, fret, and the pitch class there.</summary>
    public readonly struct NeckNote
    {
        public readonly int S;
        public readonly int F;
        public readonly int Pc;

        public NeckNote(int s, int f, int pc)
        {
            S = s;
            F = f;
            Pc = pc;
        }
    }

    /// <summary>One copy of a position: two box notes per string, plus the scale-completers inside its frets.</summary>
    public sealed class PositionInstance
    {
        public int K;
        public int Oct;
        public NeckNote[] Notes;
        /// <summary>The two box frets on each string, lowest string first.</summary>
        public (int Low, int High)[] PerString;
        public int Lo;
        public int Hi;
        public NeckNote[] Mode;

        public double Centre => (Lo + Hi) / 2.0;
    }

    /// <summary>A chord read from its notes: root, third (3 or 4 semitones, or 0 for none), fifth and suffix.</summary>
    public readonly struct Triad
    {
        public readonly int Root;
        /// <summary>3 or 4 semitones above the root, or 0 when the chord has no third.</summary>
        public readonly int Third;
        /// <summary>6, 7 or 8 semitones above the root, or 0 when it has no fifth.</summary>
        public readonly int Fifth;
        public readonly string Suffix;

        public Triad(int root, int third, int fifth, string suffix)
        {
            Root = root;
            Third = third;
            Fifth = fifth;
            Suffix = suffix;
        }
    }

    public static class FretNumbers
    {
        /// <summary>
        /// Number for every semitone above the 1, counting from a major 1. Notes
        /// outside a major key are spelled the way players say blue notes (♭3,
        /// ♭7), with ♯4 for the tritone.
        /// </summary>
        public static readonly string[] LabelsMajor = { "1", "♭2", "2", "♭3", "3", "4", "♯4", "5", "♭6", "6", "♭7", "7" };

        /// <summary>Counting from a minor 1: 1 2 ♭3 4 5 ♭6 ♭7, with the raised notes reading 3, 6 and 7.</summary>
        public static readonly string[] LabelsMinor = { "1", "♭2", "2", "♭3", "3", "4", "♭5", "5", "♭6", "6", "♭7", "7" };

        /// <summary>Minor pentatonic from its root: 1 ♭3 4 5 ♭7.</summary>
        public static readonly int[] PentatonicOffsets = { 0, 3, 5, 7, 10 };

        /// <summary>Octave colours, low to high (0xRRGGBB); a new one starts at each 1.</summary>
        public static readonly uint[] OctaveColors = { 0xfb7185, 0xfbbf24, 0x34d399, 0x38bdf8, 0xc084fc };

        /// <summary>Chord-tone ring colours: cyan root, rose third, violet fifth.</summary>
        public const uint ToneRootColor = 0x22d3ee;
        public const uint ToneThirdColor = 0xff7aa2;
        public const uint ToneFifthColor = 0xa78bfa;

        /// <summary>Notes outside the key.</summary>
        public const uint ChromaticColor = 0xffb547;

        static int Mod(int a, int n) => ((a % n) + n) % n;

        /// <summary>JavaScript's Math.floor(a / b) for integers: rounds toward minus infinity.</summary>
        static int FloorDiv(int a, int b) => (int)Math.Floor(a / (double)b);

        public static NumberFrame Frame(SongKey key, CountFrom count = CountFrom.Song)
        {
            var majorPc = key.Minor ? Mod(key.PitchClass + 3, 12) : Mod(key.PitchClass, 12);
            var boxRoot = Mod(majorPc + 9, 12);
            var minor = key.Minor != (count == CountFrom.Relative);
            return new NumberFrame(minor ? boxRoot : majorPc, minor, majorPc, boxRoot);
        }

        /// <summary>The number for a pitch (class) in the frame.</summary>
        public static string DegreeLabel(NumberFrame frame, int pitch) =>
            (frame.Minor ? LabelsMinor : LabelsMajor)[Mod(pitch - frame.Tonic, 12)];

        public static NumberTier TierOf(NumberFrame frame, int pitch)
        {
            var pc = Mod(pitch, 12);
            if (pc == frame.Tonic) return NumberTier.Root;
            foreach (var o in PentatonicOffsets)
            {
                if (Mod(frame.BoxRoot + o, 12) == pc) return NumberTier.Box;
            }
            // The 4 and 7 of the major key: ♭6 and 2 of the relative minor.
            if (pc == Mod(frame.MajorPc + 5, 12) || pc == Mod(frame.MajorPc + 11, 12)) return NumberTier.Mode;
            return NumberTier.Chrom;
        }

        /// <summary>Which octave colour a pitch takes: bands start at each 1, counted up from the lowest string.</summary>
        public static int OctaveBand(NumberFrame frame, int lowestOpen, int pitch)
        {
            var band = FloorDiv(pitch - frame.Tonic, 12) - FloorDiv(lowestOpen - frame.Tonic, 12);
            return Math.Max(0, Math.Min(OctaveColors.Length - 1, band));
        }

        static bool IsPentatonic(NumberFrame frame, int pc)
        {
            foreach (var o in PentatonicOffsets)
            {
                if (Mod(frame.BoxRoot + o, 12) == pc) return true;
            }
            return false;
        }

        /// <summary>One copy of position k (1..5), <paramref name="oct"/> octaves up from where it starts on the lowest string.</summary>
        public static PositionInstance Position(NumberFrame frame, IReadOnlyList<int> open, int k, int oct)
        {
            var n = open.Count;
            var rootFret = Mod(frame.BoxRoot - open[0], 12);
            var p = open[0] + rootFret + PentatonicOffsets[k - 1] + 12 * oct;
            var pitches = new int[2 * n];
            var found = 0;
            while (found < 2 * n)
            {
                if (IsPentatonic(frame, Mod(p, 12))) pitches[found++] = p;
                p += 1;
            }

            var notes = new NeckNote[2 * n];
            var perString = new (int, int)[n];
            var lo = int.MaxValue;
            var hi = int.MinValue;
            for (var s = 0; s < n; s += 1)
            {
                var a = pitches[2 * s] - open[s];
                var b = pitches[2 * s + 1] - open[s];
                perString[s] = (a, b);
                notes[2 * s] = new NeckNote(s, a, Mod(open[s] + a, 12));
                notes[2 * s + 1] = new NeckNote(s, b, Mod(open[s] + b, 12));
                lo = Math.Min(lo, Math.Min(a, b));
                hi = Math.Max(hi, Math.Max(a, b));
            }

            var mode = new List<NeckNote>();
            for (var s = 0; s < n; s += 1)
            {
                for (var f = lo; f <= hi; f += 1)
                {
                    var pc = Mod(open[s] + f, 12);
                    if (TierOf(frame, pc) == NumberTier.Mode && perString[s].Item1 != f && perString[s].Item2 != f)
                    {
                        mode.Add(new NeckNote(s, f, pc));
                    }
                }
            }

            return new PositionInstance
            {
                K = k,
                Oct = oct,
                Notes = notes,
                PerString = perString,
                Lo = lo,
                Hi = hi,
                Mode = mode.ToArray(),
            };
        }

        /// <summary>Every copy of position k that fits on a neck of <paramref name="maxFret"/> frets.</summary>
        public static List<PositionInstance> Positions(NumberFrame frame, IReadOnlyList<int> open, int k, int maxFret)
        {
            var output = new List<PositionInstance>();
            for (var oct = -1; oct <= 3; oct += 1)
            {
                var inst = Position(frame, open, k, oct);
                if (inst.Lo >= 0 && inst.Hi <= maxFret) output.Add(inst);
            }
            return output;
        }

        /// <summary>The copy of position k nearest a fret.</summary>
        public static PositionInstance InstanceNear(NumberFrame frame, IReadOnlyList<int> open, int k, double fret, int maxFret)
        {
            var all = Positions(frame, open, k, maxFret);
            if (all.Count == 0) return Position(frame, open, k, 0);
            var best = all[0];
            foreach (var inst in all)
            {
                if (Math.Abs(inst.Centre - fret) < Math.Abs(best.Centre - fret)) best = inst;
            }
            return best;
        }

        /// <summary>The positions either side of k on the neck: 1 sits between 5 below and 2 above.</summary>
        public static (int Below, int Above) NeighbourPositions(int k) =>
            (k == 1 ? 5 : k - 1, k == 5 ? 1 : k + 1);

        public static PositionInstance[] Neighbours(NumberFrame frame, IReadOnlyList<int> open, PositionInstance inst, int maxFret)
        {
            var (below, above) = NeighbourPositions(inst.K);
            return new[]
            {
                InstanceNear(frame, open, below, inst.Lo - 2, maxFret),
                InstanceNear(frame, open, above, inst.Hi + 2, maxFret),
            };
        }

        /// <summary>Every in-key note between two frets: the box notes and the two that complete the scale.</summary>
        public static List<NeckNote> KeyNotes(NumberFrame frame, IReadOnlyList<int> open, double f0, double f1)
        {
            var output = new List<NeckNote>();
            for (var s = 0; s < open.Count; s += 1)
            {
                for (var f = Math.Max(0, (int)Math.Floor(f0)); f <= f1; f += 1)
                {
                    var pc = Mod(open[s] + f, 12);
                    if (TierOf(frame, pc) != NumberTier.Chrom) output.Add(new NeckNote(s, f, pc));
                }
            }
            return output;
        }

        /// <summary>
        /// How strongly a fret reads: 1 inside a focused box, easing off with
        /// distance to a floor of 0.2 that never reaches zero.
        /// </summary>
        public static double FocusWeight(double fret, IReadOnlyList<(double Lo, double Hi)> windows)
        {
            var best = 0.0;
            foreach (var (lo, hi) in windows)
            {
                var d = fret < lo ? lo - fret : fret > hi ? fret - hi : 0;
                best = Math.Max(best, 1 - d * 0.12);
            }
            return Math.Max(0.2, best);
        }

        /// <summary>The same, for one window, without building a list.</summary>
        public static double FocusWeight(double fret, double lo, double hi)
        {
            var d = fret < lo ? lo - fret : fret > hi ? fret - hi : 0;
            return Math.Max(0.2, 1 - d * 0.12);
        }

        /// <summary>
        /// The position a hand is in: the copy of a box that overlaps the hand's
        /// window (fret .. fret + width - 1) by the most frets, nearest first on a
        /// tie. Position 1 when nothing on the neck fits at all.
        /// </summary>
        public static PositionInstance PositionForHand(NumberFrame frame, IReadOnlyList<int> open,
                                                       int handFret, int handWidth, int maxFret)
        {
            var handLo = handFret;
            var handHi = handFret + Math.Max(1, handWidth) - 1;
            var handCentre = (handLo + handHi) / 2.0;
            PositionInstance best = null;
            var bestOverlap = 0;
            var bestD = 0.0;
            for (var k = 1; k <= 5; k += 1)
            {
                foreach (var inst in Positions(frame, open, k, maxFret))
                {
                    var overlap = Math.Max(0, Math.Min(handHi, inst.Hi) - Math.Max(handLo, inst.Lo) + 1);
                    var d = Math.Abs(inst.Centre - handCentre);
                    if (best == null || overlap > bestOverlap || (overlap == bestOverlap && d < bestD))
                    {
                        best = inst;
                        bestOverlap = overlap;
                        bestD = d;
                    }
                }
            }
            return best ?? InstanceNear(frame, open, 1, handCentre, maxFret);
        }

        // Most specific first; extra notes (sevenths, added tones) are allowed but cost.
        static readonly (int[] Intervals, int Third, int Fifth, string Suffix)[] Triads =
        {
            (new[] { 0, 4, 7 }, 4, 7, ""),
            (new[] { 0, 3, 7 }, 3, 7, "m"),
            (new[] { 0, 3, 6 }, 3, 6, "°"),
            (new[] { 0, 4, 8 }, 4, 8, "+"),
            (new[] { 0, 5, 7 }, 0, 7, "sus4"),
            (new[] { 0, 2, 7 }, 0, 7, "sus2"),
            // A power chord has no third; Nashville writes it as the bare number.
            (new[] { 0, 7 }, 0, 7, ""),
        };

        /// <summary>Name a chord's root and quality from its pitches; null for a single pitch class or no match.</summary>
        public static Triad? TriadOf(IReadOnlyList<int> pitches)
        {
            if (pitches == null || pitches.Count == 0) return null;
            var lowest = int.MaxValue;
            var pcs = new List<int>();
            foreach (var p in pitches)
            {
                lowest = Math.Min(lowest, p);
                var pc = Mod(p, 12);
                if (!pcs.Contains(pc)) pcs.Add(pc);
            }
            var bass = Mod(lowest, 12);
            if (pcs.Count < 2) return null;

            Triad? best = null;
            var bestScore = double.NegativeInfinity;
            var intervals = new HashSet<int>();
            foreach (var root in pcs)
            {
                intervals.Clear();
                foreach (var pc in pcs) intervals.Add(Mod(pc - root, 12));
                for (var i = 0; i < Triads.Length; i += 1)
                {
                    var t = Triads[i];
                    var all = true;
                    foreach (var iv in t.Intervals)
                    {
                        if (!intervals.Contains(iv)) { all = false; break; }
                    }
                    if (!all) continue;
                    // Every note explained first, then the more specific shape, then the bass as root.
                    double score = (intervals.Count - t.Intervals.Length) * -100 + (Triads.Length - i) + (root == bass ? 50 : 0);
                    if (score > bestScore)
                    {
                        bestScore = score;
                        best = new Triad(root, t.Third, t.Fifth, t.Suffix);
                    }
                }
            }
            return best;
        }

        /// <summary>The chord's Nashville number in the frame: 4, 6m, and in minor ♭3, ♭7.</summary>
        public static string ChordNumber(NumberFrame frame, Triad triad) => DegreeLabel(frame, triad.Root) + triad.Suffix;

        /// <summary>A note's job in the chord ("R", "3", "♭3", "5", "♭5", "♯5"), or null.</summary>
        public static string ChordTone(Triad triad, int pitch)
        {
            var d = Mod(pitch - triad.Root, 12);
            if (d == 0) return "R";
            if (triad.Third != 0 && d == triad.Third) return triad.Third == 3 ? "♭3" : "3";
            if (triad.Fifth != 0 && d == triad.Fifth) return triad.Fifth == 7 ? "5" : triad.Fifth == 6 ? "♭5" : "♯5";
            return null;
        }

        public static uint ToneColor(string tone) =>
            tone == "R" ? ToneRootColor : tone == "3" || tone == "♭3" ? ToneThirdColor : ToneFifthColor;
    }
}
