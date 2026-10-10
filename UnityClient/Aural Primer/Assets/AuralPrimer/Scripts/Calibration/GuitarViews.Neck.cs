// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The two views drawn as dots on the real neck, hovering just over the
// strings at the real frets:
//
//   Neck coach      sounding notes lit with their label, the next ones ghosted
//                   with a ring that closes as they arrive, the hand window and
//                   the chord shape being held. Sparse on purpose.
//
//   Nashville neck  every number in the key around the hand's pentatonic box,
//                   brightest in the box and fading across its neighbours to a
//                   floor: a position is a focus, not a fence. The sounding note
//                   lights and every other octave of it is ringed.
//
// Both follow guitarViews.ts (drawCoach, drawNashville) and draw through the
// same FretNumbers / FretLooks the desktop's model is ported to.

using System.Collections.Generic;
using AuralPrimer.Link;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed partial class GuitarViews
    {
        /// <summary>Seconds ahead the coach shows a coming note, at a spacing of 1.</summary>
        const float CoachLeadSeconds = 1.6f;
        /// <summary>Seconds ahead the Nashville neck rings the next note.</summary>
        const float NashvilleLeadSeconds = 0.9f;
        /// <summary>Frets of the neck the Nashville view numbers at once, around the focused box.</summary>
        const int NashvilleWindowFrets = 11;

        static readonly Color ShapeColour = Hex(0xf0ecdc);

        /// <summary>Each hand position's box and its neighbours, worked out once per chart and key.</summary>
        readonly List<(float T, PositionInstance Box, PositionInstance Below, PositionInstance Above)> _handBoxes = new();
        /// <summary>Every in-key note on the neck, once per key; the view filters it to its window.</summary>
        readonly List<NeckNote> _keyNotes = new();
        /// <summary>The Nashville window's frets, eased toward the focused box.</summary>
        float _nashLo = -1f;
        float _nashHi = -1f;
        /// <summary>String-and-fret spots already drawn this frame, so a repeated note is one dot.</summary>
        readonly HashSet<int> _spots = new();

        void RebuildNashville()
        {
            _handBoxes.Clear();
            _keyNotes.Clear();
            _nashLo = _nashHi = -1f;
            if (!_keyFrame.HasValue || _chart == null || _open.Length == 0 || _profile == null) return;

            var frame = _keyFrame.Value;
            var maxFret = _profile.fretCount;
            foreach (var hand in _chart.Anchors)
            {
                var box = FretNumbers.PositionForHand(frame, _open, hand.Fret, hand.Width, maxFret);
                var around = FretNumbers.Neighbours(frame, _open, box, maxFret);
                _handBoxes.Add((hand.T, box, around[0], around[1]));
            }
            _keyNotes.AddRange(FretNumbers.KeyNotes(frame, _open, 0, maxFret));
        }

        (float T, PositionInstance Box, PositionInstance Below, PositionInstance Above) HandBoxAt(float t)
        {
            var found = _handBoxes[0];
            foreach (var entry in _handBoxes)
            {
                if (entry.T > t) break;
                found = entry;
            }
            return found;
        }

        // ---- Neck coach ----------------------------------------------------------

        void DrawCoach(float now, float visibility, FrettedChart.Anchor hand)
        {
            var camera = Camera.main;

            // The shape being held, outlined.
            var shape = _chart.HandshapeAt(now + 0.25f);
            if (shape.HasValue && now <= shape.Value.End + 0.6f)
            {
                var frets = shape.Value.Frets;
                for (var s = 0; s < frets.Length && s < _profile.stringCount; s++)
                {
                    if (frets[s] <= 0) continue;
                    Sprite(NeckPoint(s, frets[s]), DotRadius(frets[s]) * 2.9f, DotTexture(DotKind.Dashed),
                           ShapeColour, 0.55f * visibility, camera, 0f);
                }
            }

            // Next notes count down in place.
            var lead = CoachLeadSeconds / Spacing();
            var notes = _chart.Notes;
            _spots.Clear();
            for (var i = _cursor; i < notes.Count; i++)
            {
                var note = notes[i];
                if (note.On > now + lead) break;
                if (note.On <= now || !note.IsPlaced || note.String >= _profile.stringCount) continue;
                if (!_spots.Add(note.String * 64 + note.Fret)) continue;

                var colour = Hex(NoteLookFor(note).Colour);
                var progress = 1f - (note.On - now) / lead;
                var position = NeckPoint(note.String, note.Fret);
                var radius = DotRadius(note.Fret);
                Sprite(position, radius * 2f, DotTexture(DotKind.Disc), colour,
                       (0.12f + 0.2f * progress) * visibility * neckDotAlpha, camera, 0f);
                CountdownRing(position, radius, progress, colour, 0.9f * visibility, camera);
            }

            DrawSounding(now, visibility, camera, 0, _profile.fretCount, ringOctaves: _numbers.HasValue, nashville: false);
        }

        /// <summary>
        /// The notes sounding now, lit at their place on the neck.
        /// </summary>
        /// <remarks>
        /// Shared by the coach, the Nashville neck and the floating views -- for
        /// those the real neck is what the desktop's mini-neck strip was, so it
        /// shows where the lanes' and tab's notes are played. With numbers on,
        /// every other copy of a sounding number in view is ringed: any octave
        /// counts.
        /// </remarks>
        void DrawSounding(float now, float visibility, Camera camera, int f0, int f1, bool ringOctaves, bool nashville)
        {
            var notes = _chart.Notes;
            for (var i = _cursor; i < notes.Count; i++)
            {
                var note = notes[i];
                if (note.On > now) break;
                if (now >= note.Off || !note.IsPlaced || note.String >= _profile.stringCount) continue;

                if (ringOctaves && !note.IsMuted)
                {
                    for (var s = 0; s < _profile.stringCount && s < _open.Length; s++)
                    {
                        for (var fr = f0; fr <= f1; fr++)
                        {
                            if ((_open[s] + fr - note.Pitch) % 12 != 0 || (s == note.String && fr == note.Fret)) continue;
                            Sprite(NeckPoint(s, fr), DotRadius(fr) * 2.7f, DotTexture(DotKind.Dashed), Paper,
                                   0.8f * visibility, camera, 0.0005f);
                        }
                    }
                }

                if (note.Fret < f0 || note.Fret > f1) continue;
                NoteLook look;
                if (nashville && _keyFrame.HasValue)
                {
                    var frame = _keyFrame.Value;
                    var triad = TriadFor(note);
                    look = _numbers.HasValue && triad.HasValue && !note.IsMuted
                        ? FretLooks.PitchLook(frame, LowestOpen, note.Pitch, triad, Chords)
                        : FretLooks.ForNeck(frame, _numbers.HasValue, LowestOpen, note.Fret, note.Pitch, FretLabel(note));
                }
                else
                {
                    look = NoteLookFor(note);
                    // Frets light every sounding dot solid; numbers keep the tier's shape.
                    if (!_numbers.HasValue) look = look.WithShape(DotShape.Fill);
                }
                DrawDot(NeckPoint(note.String, note.Fret), DotRadius(note.Fret) * 1.18f, look, visibility, camera,
                        glow: true, toward: 0.0015f);
            }
        }

        // ---- Nashville neck ----------------------------------------------------------

        void DrawNashville(float now, float visibility)
        {
            var frame = _keyFrame.Value;
            var camera = Camera.main;
            var entry = HandBoxAt(now + 0.2f);
            var box = entry.Box;

            // The window eases toward the box rather than jumping, so the bright
            // stretch of neck slides into the next box as the hand moves.
            if (_nashLo < 0f)
            {
                _nashLo = box.Lo;
                _nashHi = box.Hi;
            }
            var ease = Mathf.Min(1f, Time.deltaTime * 5f);
            _nashLo += (box.Lo - _nashLo) * ease;
            _nashHi += (box.Hi - _nashHi) * ease;
            var f0 = Mathf.Max(0, Mathf.FloorToInt(_nashLo) - 3);
            var f1 = Mathf.Min(_profile.fretCount, f0 + NashvilleWindowFrets);

            DrawBox(entry.Below, f0, f1, 0.05f * visibility);
            DrawBox(entry.Above, f0, f1, 0.05f * visibility);
            DrawBox(box, f0, f1, 0.11f * visibility);

            var numbersOn = _numbers.HasValue;
            foreach (var spot in _keyNotes)
            {
                if (spot.F < f0 || spot.F > f1 || spot.S >= _profile.stringCount) continue;
                var weight = (float)FretNumbers.FocusWeight(spot.F, _nashLo, _nashHi);
                var look = FretLooks.ForNeck(frame, numbersOn, LowestOpen, spot.F, _open[spot.S] + spot.F, FretText(spot.F));
                var radius = DotRadius(spot.F) * (look.Shape == DotShape.Hollow ? 0.78f : 1f) * (0.72f + 0.28f * weight);
                DrawDot(NeckPoint(spot.S, spot.F), radius, look, neckDotAlpha * (0.25f + 0.75f * weight) * visibility, camera);
            }

            // The next notes: a ring that closes as they arrive.
            var lead = NashvilleLeadSeconds / Spacing();
            var notes = _chart.Notes;
            var next = _cursor;
            while (next < notes.Count && notes[next].On <= now) next++;
            if (next < notes.Count && notes[next].On - now < lead)
            {
                var first = notes[next].On;
                var progress = 1f - (first - now) / lead;
                for (var i = next; i < notes.Count && notes[i].On - first < 0.03f; i++)
                {
                    var note = notes[i];
                    if (!note.IsPlaced || note.Fret < f0 || note.Fret > f1 || note.String >= _profile.stringCount) continue;
                    CountdownRing(NeckPoint(note.String, note.Fret), DotRadius(note.Fret), progress, Paper, 0.9f * visibility, camera);
                }
            }

            DrawSounding(now, visibility, camera, f0, f1, ringOctaves: true, nashville: true);

            // Which position the hand is in, beside the box on the player's side.
            var label = _labels.Next();
            label.text = PositionText(box.K);
            label.fontSize = labelSize;
            var lowX = NeckPoint(0, Mathf.Max(0, box.Lo)).x;
            var highX = NeckPoint(0, box.Hi).x;
            var edge = _geometry.SpreadAt(highX) * 0.5f + 0.012f;
            label.transform.localPosition = new Vector3(0.5f * (lowX + highX), liftMetres, _playerSide * edge);
            Billboard(label.transform, camera);
            var accent = Accent;
            accent.a = 0.9f * visibility;
            label.color = accent;
        }

        static readonly string[] PositionTexts = { "", "POSITION 1", "POSITION 2", "POSITION 3", "POSITION 4", "POSITION 5" };

        static string PositionText(int k) => k >= 1 && k <= 5 ? PositionTexts[k] : "";

        /// <summary>
        /// A box drawn as a faint band along each string between its two dots:
        /// together they read as the box's shape without outlining over the hand.
        /// </summary>
        void DrawBox(PositionInstance box, int f0, int f1, float alpha)
        {
            if (box == null || box.Hi < f0 || box.Lo > f1) return;
            for (var s = 0; s < box.PerString.Length && s < _profile.stringCount; s++)
            {
                var (low, high) = box.PerString[s];
                var pad = DotRadius(high) * 1.2f;
                var x0 = NeckPoint(s, low).x - pad;
                var x1 = NeckPoint(s, high).x + pad;
                var middle = 0.5f * (x0 + x1);
                var band = _cubes.Next();
                band.transform.localPosition = new Vector3(middle, liftMetres - 0.0015f, _geometry.StringZ(s, middle));
                band.transform.localRotation = Quaternion.identity;
                band.transform.localScale = new Vector3(Mathf.Max(0.002f, x1 - x0), 0.0006f, StringGap(middle) * 0.9f);
                Tint(band, Accent, alpha);
            }
        }
    }
}
