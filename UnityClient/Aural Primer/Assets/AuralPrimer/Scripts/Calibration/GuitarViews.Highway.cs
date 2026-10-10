// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Neck highway: notes travel in along a lane hinged at the neck, leaned toward
// the player, and land on the string and fret the tab says -- the mock-up's
// perspective highway, on a real neck.

using System.Collections.Generic;
using AuralPrimer.Link;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed partial class GuitarViews
    {
        [Header("Neck highway")]
        [Tooltip("Seconds of music the lane shows at a note spacing of 1. The desktop's "
               + "note spacing divides it, exactly as on the desktop.")]
        [SerializeField] float highwayLookAheadSeconds = 2.5f;

        [Tooltip("Within this distance of the neck a gem fades toward the near opacity, so "
               + "it never hides the fingers fretting it.")]
        [SerializeField] float nearFadeMetres = 0.05f;
        [SerializeField, Range(0f, 1f)] float nearAlpha = 0.3f;
        [SerializeField, Range(0f, 1f)] float farAlpha = 0.9f;

        [Tooltip("How long the landing flash lasts.")]
        [SerializeField] float flashSeconds = 0.18f;

        [Tooltip("Shortest note that gets a sustain ribbon.")]
        [SerializeField] float minimumSustainSeconds = 0.12f;

        [Tooltip("Gems are pooled; this bounds how many are drawn at once.")]
        [SerializeField] int maxGems = 48;
        [SerializeField] int maxChordFrames = 6;

        /// <summary>Chord frames collected while placing gems: extent in the neck plane, and distance out.</summary>
        readonly Dictionary<int, (float minX, float maxX, float minZ, float maxZ, float distance, string name)> _frames = new();

        void DrawHighway(float now, float visibility, FrettedChart.Anchor hand)
        {
            var notes = _chart.Notes;
            // The desktop's semantics: the lane keeps its length and shows
            // lookAhead / spacing seconds of music.
            var window = highwayLookAheadSeconds / Spacing();
            var metresPerSecond = _profile.laneLengthMetres / Mathf.Max(0.05f, window);

            var axis = GuitarAnchor.ToUnity(FretboardGeometry.TimeAxis(_profile.laneTiltDegrees, _playerSide));
            var gemRotation = Quaternion.LookRotation(Vector3.Cross(Vector3.right, axis), axis);

            _frames.Clear();
            var gems = 0;
            var camera = Camera.main;

            for (var i = _cursor; i < notes.Count && gems < maxGems; i++)
            {
                var note = notes[i];
                if (note.On > now + window) break;
                if (!note.IsPlaced || note.String >= _profile.stringCount) continue;

                var untilOnset = note.On - now;
                var point = ArrivalPoint(note, hand);
                var look = NoteLookFor(note);
                var colour = Hex(look.Colour);
                // Hollow and dashed read as fainter fills on a solid gem.
                var shapeAlpha = look.Shape == DotShape.Fill ? 1f : 0.5f;

                // Sustain ribbon: from the gem back along the lane to where the
                // note ends. Once struck it shrinks into the string.
                if (note.Off - note.On >= minimumSustainSeconds && note.Off > now)
                {
                    var near = Mathf.Max(0f, untilOnset) * metresPerSecond;
                    var far = Mathf.Min(_profile.laneLengthMetres, (note.Off - now) * metresPerSecond);
                    if (far > near + 0.001f)
                    {
                        var ribbon = _cubes.Next();
                        ribbon.transform.localPosition = point + axis * (0.5f * (near + far));
                        ribbon.transform.localRotation = gemRotation;
                        ribbon.transform.localScale = new Vector3(0.002f, far - near, 0.002f);
                        Tint(ribbon, colour, 0.5f * visibility * NearFade(near));
                    }
                }

                if (untilOnset < -lateGraceSeconds) continue;

                var distance = Mathf.Max(0f, untilOnset) * metresPerSecond;
                var alpha = NearFade(distance) * visibility;
                var position = point + axis * distance;

                var gem = _cubes.Next();
                gem.transform.localPosition = position;
                gem.transform.localRotation = gemRotation;
                gem.transform.localScale = new Vector3(GemWidth(note, hand), 0.004f, StringGap(point.x) * 0.8f);
                Tint(gem, colour, alpha * shapeAlpha);

                if (look.WhiteRing || look.ToneRing != 0)
                {
                    // The 1, or a chord tone in "both": a thin rim round the gem.
                    var rim = _cubes.Next();
                    rim.transform.localPosition = position - axis * 0.0006f;
                    rim.transform.localRotation = gemRotation;
                    rim.transform.localScale = new Vector3(GemWidth(note, hand) + 0.003f, 0.002f, StringGap(point.x) * 0.8f + 0.003f);
                    Tint(rim, look.ToneRing != 0 ? Hex(look.ToneRing) : Paper, alpha);
                }

                var label = _labels.Next();
                label.text = Spell(look.Label);
                label.fontSize = labelSize;
                label.transform.localPosition = position + axis * 0.006f;
                Billboard(label.transform, camera);
                label.color = new Color(1f, 1f, 1f, alpha);
                gems++;

                // The landing, at the real string and fret.
                if (untilOnset <= 0f && untilOnset > -flashSeconds)
                {
                    var t = -untilOnset / flashSeconds;
                    var flash = _cubes.Next();
                    flash.transform.localPosition = point;
                    flash.transform.localRotation = Quaternion.identity;
                    var size = Mathf.Lerp(0.012f, 0.026f, t);
                    flash.transform.localScale = new Vector3(size, 0.0005f, size);
                    Tint(flash, colour, 0.6f * (1f - t) * visibility);
                }

                if (note.ChordGroup >= 0 && untilOnset > -0.05f)
                {
                    var half = 0.5f * GemWidth(note, hand);
                    var frame = _frames.TryGetValue(note.ChordGroup, out var f)
                        ? (Mathf.Min(f.minX, point.x - half), Mathf.Max(f.maxX, point.x + half),
                           Mathf.Min(f.minZ, point.z), Mathf.Max(f.maxZ, point.z), distance, f.name ?? ChordTitle(note))
                        : (point.x - half, point.x + half, point.z, point.z, distance, ChordTitle(note));
                    _frames[note.ChordGroup] = frame;
                }
            }

            var drawn = 0;
            foreach (var frame in _frames.Values)
            {
                if (drawn++ >= maxChordFrames) break;
                DrawChordFrame(frame, axis, visibility, camera);
            }
        }

        /// <summary>A rectangle round a chord's gems, with its name -- or number -- beside it.</summary>
        void DrawChordFrame((float minX, float maxX, float minZ, float maxZ, float distance, string name) frame,
                            Vector3 axis, float visibility, Camera camera)
        {
            const float pad = 0.005f;
            const float bar = 0.0012f;
            var y = liftMetres;
            var minX = frame.minX - pad;
            var maxX = frame.maxX + pad;
            var minZ = frame.minZ - pad;
            var maxZ = frame.maxZ + pad;
            var offset = axis * frame.distance;
            var alpha = 0.55f * visibility * NearFade(frame.distance);

            Bar(new Vector3(0.5f * (minX + maxX), y, minZ), new Vector3(maxX - minX, bar, bar));
            Bar(new Vector3(0.5f * (minX + maxX), y, maxZ), new Vector3(maxX - minX, bar, bar));
            Bar(new Vector3(minX, y, 0.5f * (minZ + maxZ)), new Vector3(bar, bar, maxZ - minZ));
            Bar(new Vector3(maxX, y, 0.5f * (minZ + maxZ)), new Vector3(bar, bar, maxZ - minZ));

            void Bar(Vector3 centre, Vector3 scale)
            {
                var r = _cubes.Next();
                r.transform.localPosition = centre + offset;
                r.transform.localRotation = Quaternion.identity;
                r.transform.localScale = scale;
                Tint(r, Paper, alpha);
            }

            if (string.IsNullOrEmpty(frame.name)) return;
            var label = _labels.Next();
            label.text = Spell(frame.name);
            label.fontSize = labelSize * 1.2f;
            // Beyond the frame on the player's side, so it reads above the chord.
            var side = _playerSide < 0 ? minZ - 0.012f : maxZ + 0.012f;
            label.transform.localPosition = new Vector3(0.5f * (minX + maxX), y, side) + offset;
            Billboard(label.transform, camera);
            label.color = new Color(1f, 1f, 1f, alpha);
        }

        /// <summary>Where a note lands, in the neck frame.</summary>
        /// <remarks>
        /// An open string has no fret to land on, so it is drawn across the
        /// current hand window, as the mock-up does: it is played wherever the
        /// hand already is.
        /// </remarks>
        Vector3 ArrivalPoint(FrettedChart.Note note, FrettedChart.Anchor hand)
        {
            var x = note.Fret > 0
                ? _geometry.FretCentreX(note.Fret)
                : 0.5f * (_geometry.FretX(hand.Fret - 1) + _geometry.FretX(hand.LastFret));
            return new Vector3(x, liftMetres, _geometry.StringZ(note.String, x));
        }

        float GemWidth(FrettedChart.Note note, FrettedChart.Anchor hand)
        {
            if (note.Fret > 0)
            {
                return Mathf.Clamp((_geometry.FretX(note.Fret) - _geometry.FretX(note.Fret - 1)) * 0.7f, 0.008f, 0.024f);
            }
            var window = _geometry.FretX(hand.LastFret) - _geometry.FretX(hand.Fret - 1);
            return Mathf.Clamp(window * 0.9f, 0.02f, 0.12f);
        }

        float StringGap(float x) =>
            _profile.stringCount > 1 ? _geometry.SpreadAt(x) / (_profile.stringCount - 1) : 0.008f;

        float NearFade(float distance) =>
            Mathf.Lerp(nearAlpha, farAlpha, Mathf.SmoothStep(0f, 1f, distance / Mathf.Max(1e-4f, nearFadeMetres)));
    }
}
