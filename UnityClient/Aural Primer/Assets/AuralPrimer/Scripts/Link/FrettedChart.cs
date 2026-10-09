// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// A guitar or bass CHART: which string and fret every note is played on.
// See docs/mr-link-protocol.md §4 ("Fretted parts") -- that is the contract.
//
// Free of UnityEngine, like MrProtocol, so the parsing and the hand-position
// rules are tested in a plain runner rather than discovered on a headset.

using System;
using System.Collections.Generic;

namespace AuralPrimer.Link
{
    public sealed class FrettedChart
    {
        /// <summary>One note, and where on the neck it is played.</summary>
        public readonly struct Note
        {
            public readonly float On;
            public readonly float Off;
            public readonly int Pitch;
            /// <summary>String index, 0 = the lowest string. -1 when the tab never placed it.</summary>
            public readonly int String;
            /// <summary>Fret, 0 = open. -1 when the tab never placed it.</summary>
            public readonly int Fret;
            /// <summary>Fretting finger 1..4, or 0 when the tab does not say.</summary>
            public readonly int Finger;
            /// <summary>Chord name, or null.</summary>
            public readonly string Chord;
            /// <summary>Feedpak technique keys present on the note; never null.</summary>
            public readonly string[] Tech;
            /// <summary>
            /// Notes struck together share a group id, -1 for a note struck alone.
            /// </summary>
            public readonly int ChordGroup;

            public Note(float on, float off, int pitch, int @string, int fret,
                        int finger = 0, string chord = null, string[] tech = null, int chordGroup = -1)
            {
                On = on;
                Off = off;
                Pitch = pitch;
                String = @string;
                Fret = fret;
                Finger = finger;
                Chord = chord;
                Tech = tech ?? Array.Empty<string>();
                ChordGroup = chordGroup;
            }

            /// <summary>Does the tab say where this note is played?</summary>
            public bool IsPlaced => String >= 0 && Fret >= 0;

            public Note WithChordGroup(int group) =>
                new(On, Off, Pitch, String, Fret, Finger, Chord, Tech, group);
        }

        /// <summary>A hand position: index finger at <see cref="Fret"/>, spanning <see cref="Width"/> frets.</summary>
        public readonly struct Anchor
        {
            public readonly float T;
            public readonly int Fret;
            public readonly int Width;

            public Anchor(float t, int fret, int width)
            {
                T = t;
                Fret = fret;
                Width = width;
            }

            /// <summary>The lowest and highest fret the hand covers.</summary>
            public int LastFret => Fret + Math.Max(1, Width) - 1;
        }

        /// <summary>Notes struck within this of each other are one chord.</summary>
        const float ChordWindowSeconds = 0.001f;

        public string Role { get; private set; } = "";

        /// <summary>Absolute MIDI open strings, lowest first. Empty when the chart has none.</summary>
        public int[] Tuning { get; private set; } = Array.Empty<int>();

        /// <summary>Sorted by onset, as the protocol guarantees.</summary>
        public List<Note> Notes { get; } = new();

        /// <summary>Sorted by time. Never empty once parsed: derived when the chart has none.</summary>
        public List<Anchor> Anchors { get; } = new();

        /// <summary>The hand positions were worked out from the notes, not sent.</summary>
        public bool AnchorsDerived { get; private set; }

        /// <summary>Is this a guitar or bass part the neck can draw?</summary>
        /// <remarks>
        /// A keys part, or a guitar part sent to a host too old to place its
        /// notes, has nothing to put on a fret -- and drawing it by guessing a
        /// position would teach the wrong place on the neck.
        /// </remarks>
        public bool IsFretted => Tuning.Length > 0 && Notes.Exists(n => n.IsPlaced);

        public static bool IsFrettedRole(string role) =>
            role == "lead_guitar" || role == "rhythm_guitar" || role == "bass" || role == "guitar";

        /// <summary>Read a CHART payload. Null when it is not JSON at all.</summary>
        public static FrettedChart Parse(string json)
        {
            object root;
            try
            {
                root = MiniJson.Parse(json);
            }
            catch (FormatException)
            {
                return null;
            }
            if (root is not Dictionary<string, object> obj) return null;

            var chart = new FrettedChart
            {
                Role = obj.TryGetValue("role", out var role) && role is string r ? r : "",
            };

            if (obj.TryGetValue("tuning", out var tuning) && tuning is List<object> strings)
            {
                var open = new List<int>();
                foreach (var s in strings)
                {
                    if (s is double d) open.Add((int)Math.Round(d));
                }
                // All or nothing: a tuning with a hole in it would misname every
                // string above the hole.
                if (open.Count == strings.Count) chart.Tuning = open.ToArray();
            }

            if (obj.TryGetValue("notes", out var notes) && notes is List<object> list)
            {
                foreach (var item in list)
                {
                    if (item is not Dictionary<string, object> n) continue;
                    if (!TryNumber(n, "on", out var on) || !TryNumber(n, "pitch", out var pitch)) continue;
                    var off = TryNumber(n, "off", out var o) ? o : on;
                    var placed = TryNumber(n, "s", out var s) & TryNumber(n, "f", out var f);
                    chart.Notes.Add(new Note(
                        (float)on, (float)off, (int)Math.Round(pitch),
                        placed ? (int)Math.Round(s) : -1,
                        placed ? (int)Math.Round(f) : -1,
                        TryNumber(n, "finger", out var finger) ? (int)Math.Round(finger) : 0,
                        n.TryGetValue("chord", out var chord) ? chord as string : null,
                        ReadStrings(n, "tech")));
                }
            }
            chart.Notes.Sort((a, b) => a.On.CompareTo(b.On));
            MarkChords(chart.Notes);

            if (obj.TryGetValue("anchors", out var anchors) && anchors is List<object> positions)
            {
                foreach (var item in positions)
                {
                    if (item is not Dictionary<string, object> a) continue;
                    if (!TryNumber(a, "t", out var t) || !TryNumber(a, "fret", out var fret)) continue;
                    var width = TryNumber(a, "width", out var w) && w >= 1 ? (int)Math.Round(w) : 4;
                    chart.Anchors.Add(new Anchor((float)t, (int)Math.Round(fret), width));
                }
                chart.Anchors.Sort((a, b) => a.T.CompareTo(b.T));
            }

            if (chart.Anchors.Count == 0)
            {
                chart.Anchors.AddRange(DeriveAnchors(chart.Notes));
                chart.AnchorsDerived = true;
            }

            return chart;
        }

        /// <summary>
        /// Hand positions worked out from the notes, for a chart with none.
        /// </summary>
        /// <remarks>
        /// The rule the desktop mock-up uses (docs/guitar-prototype): a four-fret
        /// window that stays put while the notes fit in it, slides down to a
        /// note below it, and slides up just far enough to reach a note above
        /// it. Each move lands a quarter-second before the note that caused it,
        /// so the band arrives ahead of the hand rather than with it. Open
        /// strings never move the hand.
        /// </remarks>
        public static List<Anchor> DeriveAnchors(IReadOnlyList<Note> notes)
        {
            var output = new List<Anchor>();
            var current = 1;
            foreach (var note in notes)
            {
                if (!note.IsPlaced || note.Fret <= 0) continue;
                var next = current;
                if (note.Fret < current) next = note.Fret;
                else if (note.Fret > current + 3) next = note.Fret - 3;
                if (next != current || output.Count == 0)
                {
                    current = next;
                    output.Add(new Anchor(note.On - 0.25f, current, 4));
                }
            }
            if (output.Count == 0) output.Add(new Anchor(0f, 1, 4));
            return output;
        }

        /// <summary>The hand position in force at <paramref name="t"/>.</summary>
        /// <remarks>Before the first one, the first one: the hand has to be somewhere.</remarks>
        public Anchor AnchorAt(float t)
        {
            if (Anchors.Count == 0) return new Anchor(0f, 1, 4);
            var found = Anchors[0];
            foreach (var anchor in Anchors)
            {
                if (anchor.T > t) break;
                found = anchor;
            }
            return found;
        }

        /// <summary>Give notes struck together a shared group id.</summary>
        /// <remarks>
        /// Only placed notes count: a chord frame is drawn round positions on
        /// the neck, and a note with no position has none to enclose.
        /// </remarks>
        static void MarkChords(List<Note> notes)
        {
            var group = 0;
            var i = 0;
            while (i < notes.Count)
            {
                var j = i + 1;
                while (j < notes.Count && notes[j].On - notes[i].On <= ChordWindowSeconds) j++;

                var placed = 0;
                for (var k = i; k < j; k++)
                {
                    if (notes[k].IsPlaced) placed++;
                }
                if (placed >= 2)
                {
                    for (var k = i; k < j; k++)
                    {
                        if (notes[k].IsPlaced) notes[k] = notes[k].WithChordGroup(group);
                    }
                    group++;
                }
                i = j;
            }
        }

        static bool TryNumber(Dictionary<string, object> obj, string key, out double value)
        {
            if (obj.TryGetValue(key, out var raw) && raw is double d && !double.IsNaN(d))
            {
                value = d;
                return true;
            }
            value = 0;
            return false;
        }

        static string[] ReadStrings(Dictionary<string, object> obj, string key)
        {
            if (!obj.TryGetValue(key, out var raw) || raw is not List<object> list) return Array.Empty<string>();
            var output = new List<string>(list.Count);
            foreach (var item in list)
            {
                if (item is string s) output.Add(s);
            }
            return output.ToArray();
        }
    }
}
