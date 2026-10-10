// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The two floating views, drawn on a board near the guitar's head end and
// turned to face the player:
//
//   String lanes  one lane per string, lowest on the left, gems falling to a
//                 hit line, labelled by fret or number.
//   Tab scroll    six lines, high e on top, labels scrolling past a playhead,
//                 bar lines from the song's bars.
//
// guitarViews.ts draws a mini-neck strip under its lanes; here the real neck
// is that strip -- it keeps lighting the notes being played.

using System.Collections.Generic;
using AuralPrimer.Link;
using TMPro;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed partial class GuitarViews
    {
        [Header("Floating views")]
        [Tooltip("The board's size, metres.")]
        [SerializeField] float boardWidthMetres = 0.36f;
        [SerializeField] float boardHeightMetres = 0.24f;

        [Tooltip("Where the board floats: beyond the nut, at the head end, and up.")]
        [SerializeField] float boardBeyondNutMetres = 0.12f;
        [SerializeField] float boardAboveNeckMetres = 0.16f;

        [Tooltip("Seconds of music the lanes show at a note spacing of 1.")]
        [SerializeField] float lanesLookAheadSeconds = 2.4f;

        [Tooltip("How fast the tab scrolls at a note spacing of 1, metres per second.")]
        [SerializeField] float tabMetresPerSecond = 0.055f;

        static readonly Color BoardColour = Hex(0x0c1120);

        Transform _panel;
        bool _panelPlaced;
        Pool<Renderer> _panelCubes;
        Pool<TextMeshPro> _panelLabels;
        Renderer _panelBack;
        readonly List<float> _barTimes = new();

        void EnsurePanel()
        {
            if (_panel != null) return;
            _panel = new GameObject("Guitar Board").transform;
            _panelCubes = new Pool<Renderer>(() => NewCube("Board mark", _panel));
            _panelLabels = new Pool<TextMeshPro>(() => NewLabel("Board label", _panel));
            _panelBack = NewCube("Board", _panel);
        }

        void BeginPanelFrame()
        {
            if (_panel == null) return;
            _panelCubes.Begin();
            _panelLabels.Begin();
        }

        void EndPanelFrame(bool used)
        {
            if (_panel == null) return;
            _panelCubes.End();
            _panelLabels.End();
            if (!used) _panel.gameObject.SetActive(false);
        }

        void HidePanel()
        {
            if (_panel == null) return;
            _panelCubes.HideAll();
            _panelLabels.HideAll();
            _panel.gameObject.SetActive(false);
        }

        /// <summary>
        /// Float the board beyond the nut and above the neck, turned to the player.
        /// </summary>
        /// <remarks>
        /// It follows the guitar, but eased: bolted to the neck it would swing
        /// with every move of the guitar and be unreadable. While the guitar is
        /// out of view it stays where it was.
        /// </remarks>
        void PlacePanel(Camera camera, float visibility)
        {
            EnsurePanel();
            if (!_panel.gameObject.activeSelf) _panel.gameObject.SetActive(true);

            _panelBack.transform.localPosition = new Vector3(0f, 0f, 0.004f);
            _panelBack.transform.localRotation = Quaternion.identity;
            _panelBack.transform.localScale = new Vector3(boardWidthMetres + 0.04f, boardHeightMetres + 0.03f, 0.001f);
            Tint(_panelBack, BoardColour, 0.6f * visibility);

            if (camera == null) return;
            var target = transform.TransformPoint(new Vector3(-boardBeyondNutMetres, 0f, 0f))
                       + Vector3.up * boardAboveNeckMetres;
            var toBoard = target - camera.transform.position;
            if (toBoard.sqrMagnitude < 1e-6f) return;
            var rotation = Quaternion.LookRotation(toBoard, Vector3.up);

            if (!_panelPlaced)
            {
                _panel.SetPositionAndRotation(target, rotation);
                _panelPlaced = true;
                return;
            }
            if (!anchor.IsTracked) return;
            var k = 1f - Mathf.Exp(-Time.deltaTime * 3f);
            _panel.SetPositionAndRotation(Vector3.Lerp(_panel.position, target, k),
                                          Quaternion.Slerp(_panel.rotation, rotation, k));
        }

        void BoardCube(Vector3 local, Vector3 scale, Color colour, float alpha)
        {
            var r = _panelCubes.Next();
            r.transform.localPosition = local;
            r.transform.localRotation = Quaternion.identity;
            r.transform.localScale = scale;
            Tint(r, colour, alpha);
        }

        void BoardLabel(string text, Vector3 local, float size, Color colour, float alpha)
        {
            var label = _panelLabels.Next();
            label.text = text;
            label.fontSize = size;
            // The board faces the player with its +Z away from them, which is
            // TMP's readable side; the labels sit just in front of it.
            label.transform.localPosition = local;
            label.transform.localRotation = Quaternion.identity;
            colour.a = Mathf.Clamp01(alpha);
            label.color = colour;
        }

        /// <summary>Bar lines between two times: the song's bars, or a constant grid from its tempo.</summary>
        void BarsBetween(float t0, float t1)
        {
            _barTimes.Clear();
            var bars = _chart.Bars;
            if (bars.Count > 0)
            {
                foreach (var bar in bars)
                {
                    if (bar < t0) continue;
                    if (bar > t1) break;
                    _barTimes.Add(bar);
                }
                return;
            }
            var period = 60f / Mathf.Max(1f, _chart.Bpm) * Mathf.Max(1, _chart.BeatsPerBar);
            for (var t = Mathf.Ceil(t0 / period) * period; t <= t1; t += period) _barTimes.Add(t);
        }

        int Strings => Mathf.Min(_profile.stringCount, _open.Length);

        // ---- String lanes ----------------------------------------------------------

        void DrawLanes(float now, float visibility)
        {
            var camera = Camera.main;
            PlacePanel(camera, visibility);

            var n = Strings;
            var width = boardWidthMetres;
            var laneW = width / Mathf.Max(1, n);
            var left = -0.5f * width;
            var hitY = -0.5f * boardHeightMetres + 0.035f;
            var top = 0.5f * boardHeightMetres - 0.005f;
            var look = lanesLookAheadSeconds / Spacing();
            var metresPerSecond = (top - hitY) / look;
            float LaneX(int s) => left + (s + 0.5f) * laneW;
            float Y(float t) => hitY + (t - now) * metresPerSecond;

            for (var s = 0; s < n; s++)
            {
                BoardCube(new Vector3(LaneX(s), 0.5f * (top + hitY), 0.001f),
                          new Vector3(0.0012f, top - hitY, 0.0005f), ForString(s), 0.32f * visibility);
                BoardLabel(FretLooks.StringLabel(_open, s), new Vector3(LaneX(s), hitY - 0.02f, -0.001f),
                           labelSize * 1.1f, ForString(s), 0.9f * visibility);
            }

            BarsBetween(now, now + look);
            foreach (var bar in _barTimes)
            {
                BoardCube(new Vector3(0f, Y(bar), 0.0015f), new Vector3(width, 0.0008f, 0.0005f), Paper, 0.22f * visibility);
            }

            BoardCube(new Vector3(0f, hitY, 0f), new Vector3(width + 0.01f, 0.0025f, 0.0005f), Paper, 0.6f * visibility);

            var notes = _chart.Notes;
            for (var i = _cursor; i < notes.Count; i++)
            {
                var note = notes[i];
                if (note.On > now + look) break;
                if (!note.IsPlaced || note.String >= n || note.Off < now - lateGraceSeconds) continue;

                var noteLook = NoteLookFor(note);
                var colour = Hex(noteLook.Colour);
                var x = LaneX(note.String);

                if (note.Off - note.On >= minimumSustainSeconds && note.Off > now)
                {
                    var y0 = Y(Mathf.Max(now, note.On));
                    var y1 = Mathf.Min(top, Y(note.Off));
                    if (y1 > y0 + 0.001f)
                    {
                        BoardCube(new Vector3(x, 0.5f * (y0 + y1), 0.0008f), new Vector3(laneW * 0.22f, y1 - y0, 0.0005f),
                                  colour, 0.45f * visibility);
                    }
                }

                var untilOnset = note.On - now;
                if (untilOnset < -lateGraceSeconds) continue;

                var y = Y(Mathf.Max(now, note.On));
                var fill = noteLook.Shape == DotShape.Fill;
                if (noteLook.WhiteRing || noteLook.ToneRing != 0)
                {
                    BoardCube(new Vector3(x, y, 0.0004f), new Vector3(laneW * 0.86f, 0.019f, 0.0005f),
                              noteLook.ToneRing != 0 ? Hex(noteLook.ToneRing) : Paper, 0.9f * visibility);
                }
                BoardCube(new Vector3(x, y, 0f), new Vector3(laneW * 0.78f, 0.014f, 0.002f), fill ? colour : BoardColour,
                          (fill ? 0.95f : 0.9f) * visibility);
                if (!fill)
                {
                    // Hollow and dashed: an outline in the colour, read against the board.
                    BoardCube(new Vector3(x, y, 0.0002f), new Vector3(laneW * 0.82f, 0.017f, 0.0005f), colour,
                              (noteLook.Shape == DotShape.Dashed ? 0.6f : 0.9f) * visibility);
                }
                BoardLabel(Spell(noteLook.Label), new Vector3(x, y, -0.0015f), labelSize * 1.1f, fill ? Ink : colour, visibility);

                if (untilOnset <= 0f)
                {
                    var t = -untilOnset / Mathf.Max(0.01f, lateGraceSeconds);
                    BoardCube(new Vector3(x, hitY, -0.0005f), new Vector3(laneW * (0.9f + 0.3f * t), 0.006f, 0.0005f),
                              colour, 0.7f * (1f - t) * visibility);
                }
            }

            DrawSounding(now, visibility, camera, 0, _profile.fretCount, _numbers.HasValue, nashville: false);
        }

        // ---- Tab scroll -------------------------------------------------------------------

        void DrawTab(float now, float visibility)
        {
            var camera = Camera.main;
            PlacePanel(camera, visibility);

            var n = Strings;
            var width = boardWidthMetres;
            var staff = boardHeightMetres * 0.7f;
            var gap = staff / Mathf.Max(1, n - 1);
            var topY = 0.5f * staff;
            float Row(int s) => topY - (n - 1 - s) * gap; // the high string on top
            var left = -0.5f * width;
            var playX = left + 0.2f * width;
            var metresPerSecond = tabMetresPerSecond * Spacing();
            var t0 = now - (playX - left) / metresPerSecond;
            var t1 = now + (0.5f * width - playX) / metresPerSecond;
            float X(float t) => playX + (t - now) * metresPerSecond;

            for (var s = 0; s < n; s++)
            {
                BoardCube(new Vector3(0f, Row(s), 0.0015f), new Vector3(width, 0.0008f, 0.0005f), Paper, 0.28f * visibility);
                BoardLabel(FretLooks.StringLabel(_open, s), new Vector3(left - 0.014f, Row(s), -0.001f),
                           labelSize, ForString(s), 0.9f * visibility);
            }

            BarsBetween(t0, t1);
            foreach (var bar in _barTimes)
            {
                BoardCube(new Vector3(X(bar), 0f, 0.0015f), new Vector3(0.0012f, staff, 0.0005f), Paper, 0.35f * visibility);
            }

            BoardCube(new Vector3(playX, 0f, 0.001f), new Vector3(0.0025f, staff + 0.025f, 0.0005f), Accent, 0.8f * visibility);

            var notes = _chart.Notes;
            // The tab keeps a little of what has passed, so step back from the cursor to the left edge.
            var start = _cursor;
            while (start > 0 && notes[start - 1].On >= t0) start--;
            for (var i = start; i < notes.Count; i++)
            {
                var note = notes[i];
                if (note.On > t1) break;
                if (note.On < t0 || !note.IsPlaced || note.String >= n) continue;

                var noteLook = NoteLookFor(note);
                var colour = Hex(noteLook.Colour);
                var x = X(note.On);
                var y = Row(note.String);
                var alpha = (note.On < now ? 0.45f : 1f) * visibility;

                if (note.Off - note.On >= minimumSustainSeconds)
                {
                    var x1 = Mathf.Min(0.5f * width, X(note.Off));
                    if (x1 > x + 0.008f)
                    {
                        BoardCube(new Vector3(0.5f * (x + x1), y, 0.001f), new Vector3(x1 - x, 0.0018f, 0.0005f), colour, 0.5f * alpha);
                    }
                }
                // A dark patch so the line does not strike through the number, as on paper tab.
                BoardCube(new Vector3(x, y, 0.0005f), new Vector3(0.013f, 0.011f, 0.0005f), BoardColour, 0.95f * visibility);
                if (noteLook.WhiteRing || noteLook.ToneRing != 0)
                {
                    BoardCube(new Vector3(x, y - 0.0068f, 0.0004f), new Vector3(0.012f, 0.0012f, 0.0005f),
                              noteLook.ToneRing != 0 ? Hex(noteLook.ToneRing) : Paper, alpha);
                }
                BoardLabel(Spell(noteLook.Label), new Vector3(x, y, -0.001f), labelSize,
                           noteLook.Shape == DotShape.Dashed ? Hex(FretNumbers.ChromaticColor) : colour, alpha);
            }

            DrawSounding(now, visibility, camera, 0, _profile.fretCount, _numbers.HasValue, nashville: false);
        }
    }
}
