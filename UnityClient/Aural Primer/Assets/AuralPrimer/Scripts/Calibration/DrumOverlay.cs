// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Cues drawn on the player's real drums.
//
// The keyboard equivalent of this file paints a bar inside each key. A kit is
// not a row of keys: the pieces sit at arbitrary positions and angles, each was
// placed by hand, and there is no layout to derive a position from -- so every
// cue is drawn from the profile's own record of where that pad is.
//
// Lane ids arrive spelled the way `drum_tab.json` spells them, which is the
// whole reason the drum chart is a frame of its own. Open and closed hi-hat are
// two pieces of hardware a foot apart; the desktop's eight-lane scheme calls
// them both HH, and a cue on the wrong one is worse than no cue.

using System;
using System.Collections.Generic;
using AuralPrimer.Link;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Drum Overlay")]
    public sealed class DrumOverlay : MonoBehaviour
    {
        [SerializeField] MrLinkBehaviour link;

        [Tooltip("How far ahead a pad starts showing that a hit is coming.")]
        [SerializeField] float previewSeconds = 1.2f;

        [Tooltip("How long the cue stays after the hit is due, so a late hand "
               + "still sees what it missed.")]
        [SerializeField] float strikeGraceSeconds = 0.12f;

        [Tooltip("How far above the pad the ring floats, so it does not fight "
               + "the real surface for the same pixels.")]
        [SerializeField] float hoverMetres = 0.01f;

        [SerializeField] Material cueMaterial;

        [Tooltip("How far ahead the column above each pad reaches. Longer than "
               + "the ring's window: the ring says which drum is next, the "
               + "column says what the bar looks like.")]
        [SerializeField] float columnSeconds = 2.5f;

        [Tooltip("How tall that column stands above the pad.")]
        [SerializeField] float columnHeightMetres = 0.5f;

        [Tooltip("Ceiling on column marks drawn at once, across the whole kit.")]
        [SerializeField] int maxColumnNotes = 128;

        [SerializeField] Material columnNoteMaterial;

        CalibrationProfile _profile;
        readonly List<DrumHit> _hits = new();
        int _cursor;

        /// <summary>One cue quad per placed piece, reused every frame.</summary>
        readonly Dictionary<string, Transform> _cues = new();
        readonly Dictionary<string, Renderer> _cueRenderers = new();
        readonly Dictionary<string, float> _soonest = new();
        /// <summary>Pooled column marks, reused frame to frame.</summary>
        readonly List<Transform> _columnPool = new();
        MaterialPropertyBlock _block;
        static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");

        /// <summary>A drum hit, in the chart's own vocabulary.</summary>
        readonly struct DrumHit
        {
            public readonly float T;
            public readonly string Lane;
            public readonly float Velocity;

            public DrumHit(float t, string lane, float velocity)
            {
                T = t;
                Lane = lane;
                Velocity = velocity;
            }
        }

        /// <summary>
        /// A pad with a hit due. Green, and green means the same thing here as
        /// it does on the key bed: play this one next.
        /// </summary>
        /// <remarks>
        /// Deliberately NOT coloured per piece. The desktop highway gives each
        /// lane its own hue, but those were chosen against a monitor and
        /// passthrough compresses chroma badly -- and more to the point, the
        /// player is looking at their own kit, which already tells them which
        /// drum is which. Hue here is free to say the one thing the kit cannot:
        /// whether this is the one to hit now.
        /// </remarks>
        static readonly Color PlayNow = new(0.337f, 0.910f, 0.522f, 1f);

        void OnEnable()
        {
            if (link != null) link.DrumChartReceived += OnDrumChart;
        }

        void OnDisable()
        {
            if (link != null) link.DrumChartReceived -= OnDrumChart;
        }

        /// <summary>
        /// Point this at the session, for an overlay built at runtime.
        /// </summary>
        /// <remarks>
        /// The scene has no drum object to serialise a reference into, and
        /// adding one would mean a scene edit that a later merge can silently
        /// drop -- the same reason the wizard panel builds its own UI in code.
        /// Re-subscribes, so binding after OnEnable still hears charts.
        /// </remarks>
        public void Bind(MrLinkBehaviour source)
        {
            if (ReferenceEquals(link, source)) return;
            if (link != null) link.DrumChartReceived -= OnDrumChart;
            link = source;
            if (link != null && isActiveAndEnabled) link.DrumChartReceived += OnDrumChart;
        }

        /// <summary>The kit to draw on. Set by the wizard once calibrated.</summary>
        public void SetProfile(CalibrationProfile profile)
        {
            _profile = profile;
            foreach (var cue in _cues.Values)
            {
                if (cue != null) Destroy(cue.gameObject);
            }
            _cues.Clear();
            _cueRenderers.Clear();
        }

        void OnDrumChart(string json)
        {
            _hits.Clear();
            _cursor = 0;
            try
            {
                ParseHits(json, _hits);
            }
            catch (Exception e)
            {
                Debug.LogError($"[drums] could not read the chart: {e.Message}");
                _hits.Clear();
            }

            var placed = _profile != null ? _profile.kitPieces.Count : 0;
            Debug.Log($"[drums] chart loaded: {_hits.Count} hits, {placed} piece(s) placed");
        }

        void Update()
        {
            if (_profile == null || link == null || _hits.Count == 0)
            {
                HideAll();
                return;
            }

            var now = (float)link.SongTimeSec;

            // Sorted by time, so a cursor walks with the song rather than
            // rescanning thousands of hits every frame. Rewound on a seek.
            while (_cursor > 0 && _hits[_cursor - 1].T >= now - strikeGraceSeconds) _cursor--;
            while (_cursor < _hits.Count && _hits[_cursor].T < now - strikeGraceSeconds) _cursor++;

            // Nearest hit per pad. A roll puts several hits on one drum inside
            // the window, and the only one worth drawing is the next one.
            // Reused rather than allocated: this runs every frame in a headset,
            // where a per-frame dictionary is garbage the collector will come
            // for mid-song.
            _soonest.Clear();
            for (var i = _cursor; i < _hits.Count; i++)
            {
                var hit = _hits[i];
                var until = hit.T - now;
                if (until > previewSeconds) break;
                if (!_soonest.ContainsKey(hit.Lane)) _soonest[hit.Lane] = until;
            }

            foreach (var pair in _cues)
            {
                if (pair.Value != null) pair.Value.gameObject.SetActive(false);
            }

            foreach (var pair in _soonest)
            {
                var piece = _profile.PieceForLane(pair.Key);
                // A song calling for a piece this kit does not have draws
                // nothing. Putting it on the nearest drum instead would be
                // teaching the player the wrong part.
                if (piece == null || !piece.IsComplete) continue;
                ShowCue(piece, pair.Value);
            }

            DrawColumns(now);
        }

        /// <summary>
        /// The marks standing above each pad, one per hit still to come.
        /// </summary>
        /// <remarks>
        /// The ring on the pad says which drum is next and cannot say what
        /// follows it -- a bar of sixteenths on the hi-hat is one ring blinking.
        /// The column is the lookahead, and it stands above the drum it belongs
        /// to, which is the thing a flat screen cannot do: the mark you are
        /// reading is directly over the drum your stick is going to.
        /// </remarks>
        void DrawColumns(float now)
        {
            var used = 0;
            var head = Camera.main;

            for (var i = _cursor; i < _hits.Count && used < maxColumnNotes; i++)
            {
                var hit = _hits[i];
                var until = hit.T - now;
                if (until > columnSeconds) break;
                if (until < 0f) continue;

                var piece = _profile.PieceForLane(hit.Lane);
                if (piece == null || !piece.IsComplete) continue;

                var mark = ColumnMark(used);
                if (mark == null) break;

                var up = piece.normal.normalized;
                var height = hoverMetres + (until / Mathf.Max(0.01f, columnSeconds)) * columnHeightMetres;
                mark.position = piece.centre + up * height;
                // Turned to face the player rather than lying flat on the pad's
                // plane: a mark half a metre up, seen edge-on, is invisible.
                mark.rotation = head != null
                    ? Quaternion.LookRotation(mark.position - head.transform.position, up)
                    : Quaternion.LookRotation(up);

                var width = Mathf.Max(0.02f, piece.RadiusMetres * 1.2f);
                mark.localScale = new Vector3(width, width * 0.18f, 1f);
                mark.gameObject.SetActive(true);
                used++;
            }

            for (var i = used; i < _columnPool.Count; i++)
            {
                if (_columnPool[i] != null && _columnPool[i].gameObject.activeSelf)
                {
                    _columnPool[i].gameObject.SetActive(false);
                }
            }
        }

        Transform ColumnMark(int index)
        {
            while (_columnPool.Count <= index)
            {
                var quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
                quad.name = $"DrumColumnMark_{_columnPool.Count}";
                var box = quad.GetComponent<Collider>();
                if (box != null) Destroy(box);
                quad.transform.SetParent(transform, worldPositionStays: true);
                var renderer = quad.GetComponent<Renderer>();
                // One flat colour for every mark. Which drum is next is the
                // ring's job, down on the pad; the column only has to show the
                // shape of what is coming.
                if (columnNoteMaterial != null) renderer.sharedMaterial = columnNoteMaterial;
                else if (cueMaterial != null) renderer.sharedMaterial = cueMaterial;
                _columnPool.Add(quad.transform);
            }
            return _columnPool[index];
        }

        void ShowCue(KitPiece piece, float untilOnset)
        {
            var cue = CueFor(piece);
            if (cue == null) return;

            // 1 at the far edge of the window, 0 at the strike.
            var nearness = 1f - Mathf.Clamp01(untilOnset / Mathf.Max(0.01f, previewSeconds));

            // The ring grows into the pad as the hit approaches, so how soon is
            // size and not colour -- the same division the key bed uses, where
            // length says when and hue says what.
            var radius = piece.RadiusMetres * Mathf.Lerp(0.25f, 1f, nearness);
            cue.localScale = new Vector3(radius * 2f, radius * 2f, 1f);
            cue.position = piece.centre + piece.normal.normalized * hoverMetres;
            cue.rotation = Quaternion.LookRotation(piece.normal.normalized);
            cue.gameObject.SetActive(true);

            if (_cueRenderers.TryGetValue(piece.id, out var renderer) && renderer != null)
            {
                _block ??= new MaterialPropertyBlock();
                _block.Clear();
                var colour = PlayNow;
                // Faint far out, solid at the strike. Opacity rather than hue,
                // for the reason the key bed learned the hard way: a translucent
                // cue over a dark drum in passthrough stops reading at all.
                colour.a = Mathf.Lerp(0.25f, 1f, nearness);
                _block.SetColor(BaseColorId, colour);
                renderer.SetPropertyBlock(_block);
            }
        }

        Transform CueFor(KitPiece piece)
        {
            if (_cues.TryGetValue(piece.id, out var existing) && existing != null) return existing;

            var quad = GameObject.CreatePrimitive(PrimitiveType.Quad);
            quad.name = $"DrumCue_{piece.id}";
            // No collider: these are drawn on top of real drums a player is
            // hitting with sticks, and a stray collider there would catch hand
            // rays aimed at anything behind them.
            var box = quad.GetComponent<Collider>();
            if (box != null) Destroy(box);
            quad.transform.SetParent(transform, worldPositionStays: true);

            var renderer = quad.GetComponent<Renderer>();
            if (cueMaterial != null) renderer.sharedMaterial = cueMaterial;

            _cues[piece.id] = quad.transform;
            _cueRenderers[piece.id] = renderer;
            return quad.transform;
        }

        void HideAll()
        {
            foreach (var cue in _cues.Values)
            {
                if (cue != null && cue.gameObject.activeSelf) cue.gameObject.SetActive(false);
            }
            for (var i = 0; i < _columnPool.Count; i++)
            {
                if (_columnPool[i] != null && _columnPool[i].gameObject.activeSelf)
                {
                    _columnPool[i].gameObject.SetActive(false);
                }
            }
        }

        /// <summary>
        /// Pull the hits out of the drum chart.
        /// </summary>
        /// <remarks>
        /// Scanned rather than deserialised, like every other payload here: the
        /// chart arrives as one large string, and a full parse to read three
        /// fields per hit would cost more than the draw loop it feeds.
        /// </remarks>
        static void ParseHits(string json, List<DrumHit> into)
        {
            if (string.IsNullOrEmpty(json)) return;
            var at = json.IndexOf("\"hits\"", StringComparison.Ordinal);
            if (at < 0) return;

            var i = json.IndexOf('[', at);
            if (i < 0) return;

            while (i < json.Length)
            {
                var open = json.IndexOf('{', i);
                if (open < 0) break;
                var close = json.IndexOf('}', open);
                if (close < 0) break;

                var span = json.Substring(open, close - open + 1);
                if (TryNumber(span, "t", out var t) && TryString(span, "p", out var lane))
                {
                    var velocity = TryNumber(span, "v", out var v) ? (float)v : 100f;
                    into.Add(new DrumHit((float)t, lane, velocity));
                }

                i = close + 1;
            }
            into.Sort((a, b) => a.T.CompareTo(b.T));
        }

        static bool TryString(string span, string key, out string value)
        {
            value = null;
            var marker = $"\"{key}\":";
            var at = span.IndexOf(marker, StringComparison.Ordinal);
            if (at < 0) return false;
            var open = span.IndexOf('"', at + marker.Length);
            if (open < 0) return false;
            var close = span.IndexOf('"', open + 1);
            if (close <= open) return false;
            value = span.Substring(open + 1, close - open - 1);
            return value.Length > 0;
        }

        static bool TryNumber(string span, string key, out double value)
        {
            value = 0;
            var marker = $"\"{key}\":";
            var at = span.IndexOf(marker, StringComparison.Ordinal);
            if (at < 0) return false;
            var start = at + marker.Length;
            var end = start;
            while (end < span.Length && (char.IsDigit(span[end]) || span[end] == '.'
                                         || span[end] == '-' || span[end] == '+'
                                         || span[end] == 'e' || span[end] == 'E'))
            {
                end++;
            }
            return end > start
                && double.TryParse(span.Substring(start, end - start),
                                   System.Globalization.NumberStyles.Float,
                                   System.Globalization.CultureInfo.InvariantCulture,
                                   out value);
        }
    }
}
