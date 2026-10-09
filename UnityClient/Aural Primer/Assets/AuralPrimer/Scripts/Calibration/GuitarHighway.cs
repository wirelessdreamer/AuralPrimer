// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Notes arriving on the real frets of a guitar or bass.
//
// Parented to GuitarAnchor, so its local frame IS the neck frame: X along the
// neck from the nut, Y out of the fretboard, Z from the lowest string to the
// highest (FretboardGeometry). Each note travels in along a time axis hinged
// at the neck and leaned toward the player, and lands on the string and fret
// the tab says -- the desktop mock-up's "Neck highway" view
// (docs/guitar-prototype/guitar-surfaces.html), on a real neck.
//
// Built for the Quest 3S / Pro floor: everything is pooled cubes and labels
// with a hard cap, one unlit transparent material, no per-note allocation.
// And it stays faint near the neck. Passthrough has no depth here, so anything
// drawn at the strings is drawn OVER the fretting hand; gems fade as they
// arrive and the landing is a brief flash, so the hand stays visible.

using System;
using System.Collections.Generic;
using System.Globalization;
using AuralPrimer.Link;
using TMPro;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Guitar Highway")]
    public sealed class GuitarHighway : MonoBehaviour
    {
        [SerializeField] MrLinkBehaviour link;
        [SerializeField] GuitarAnchor anchor;

        [Tooltip("Font for fret numbers and chord names; the TMP default when unset.")]
        [SerializeField] TMP_FontAsset labelFont;

        [Tooltip("Seconds of music the lane shows at a note spacing of 1. The desktop's "
               + "note spacing divides it, exactly as on the desktop.")]
        [SerializeField] float lookAheadSeconds = 2.5f;

        [Tooltip("Height of the landing point above the fretboard: just over the strings.")]
        [SerializeField] float liftMetres = 0.006f;

        [Tooltip("How long a gem sits on its fret after its onset before it goes.")]
        [SerializeField] float lateGraceSeconds = 0.08f;

        [Tooltip("Within this distance of the neck a gem fades toward the near opacity, so "
               + "it never hides the fingers fretting it.")]
        [SerializeField] float nearFadeMetres = 0.05f;
        [SerializeField, Range(0f, 1f)] float nearAlpha = 0.3f;
        [SerializeField, Range(0f, 1f)] float farAlpha = 0.9f;

        [Tooltip("How long the landing flash lasts.")]
        [SerializeField] float flashSeconds = 0.18f;

        [Tooltip("Shortest note that gets a sustain ribbon.")]
        [SerializeField] float minimumSustainSeconds = 0.12f;

        [Tooltip("How far ahead the hand-position band looks, so it moves before the hand must.")]
        [SerializeField] float bandLeadSeconds = 0.35f;

        [Tooltip("TMP font size of a gem's label. World-space TMP sizes are small numbers; "
               + "tune on the headset.")]
        [SerializeField] float labelSize = 0.06f;

        [Tooltip("Gems are pooled; this bounds how many exist at once.")]
        [SerializeField] int maxGems = 48;
        [SerializeField] int maxChordFrames = 6;

        /// <summary>
        /// String colours, lowest string first: the red / yellow / blue / orange /
        /// green / purple convention of the desktop mock-up and of tab highways.
        /// </summary>
        static readonly Color[] StringColours =
        {
            Hex(0xff5c7a), Hex(0xf5d547), Hex(0x38bdf8), Hex(0xfb923c), Hex(0x34d399), Hex(0xa78bfa),
        };

        /// <summary>The mock-up's accent, for the hand-position band.</summary>
        static readonly Color HandBandColour = Hex(0x22d3ee);

        public static Color ForString(int s) => StringColours[((s % StringColours.Length) + StringColours.Length) % StringColours.Length];

        /// <summary>
        /// What a gem says. The fret by default.
        /// </summary>
        /// <remarks>
        /// Pluggable on purpose: the Nashville fretboard plan
        /// (docs/guitar-nashville-fretboard-plan-2026-09-30.md) labels the same
        /// dots by scale degree, and that should be a different function here,
        /// not a different highway.
        /// </remarks>
        public Func<FrettedChart.Note, string> GemLabel { get; set; } = FretLabel;

        static string FretLabel(FrettedChart.Note note) => note.Fret.ToString(CultureInfo.InvariantCulture);

        /// <summary>Why no part is being drawn, for the menu to say; null when one is.</summary>
        public string Problem { get; private set; }

        FretboardProfile _profile;
        FretboardGeometry _geometry;
        FrettedChart _chart;
        int _cursor;
        /// <summary>Which side of the neck the player looks from: -1 toward the lowest string.</summary>
        int _playerSide = -1;

        Material _material;
        MaterialPropertyBlock _block;
        static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");

        readonly List<Renderer> _gems = new();
        readonly List<TextMeshPro> _labels = new();
        readonly List<Renderer> _ribbons = new();
        readonly List<Renderer> _flashes = new();
        readonly List<Renderer> _frameBars = new();
        readonly List<TextMeshPro> _chordNames = new();
        readonly List<Renderer> _frets = new();
        readonly List<Renderer> _strings = new();
        Renderer _band;
        TextMeshPro _notTracked;

        /// <summary>Chord frames collected while placing gems: extent in the neck plane, and distance out.</summary>
        readonly Dictionary<int, (float minX, float maxX, float minZ, float maxZ, float distance, string name)> _frames = new();

        public void Bind(MrLinkBehaviour mrLink, GuitarAnchor neckAnchor)
        {
            if (link != null) link.ChartReceived -= OnChart;
            link = mrLink;
            anchor = neckAnchor;
            if (isActiveAndEnabled && link != null) link.ChartReceived += OnChart;
        }

        void Awake()
        {
            _material = NewMaterial();
            _block = new MaterialPropertyBlock();
        }

        void OnEnable()
        {
            if (link != null) link.ChartReceived += OnChart;
        }

        void OnDisable()
        {
            if (link != null) link.ChartReceived -= OnChart;
        }

        /// <summary>Draw on this neck. Null hides everything.</summary>
        public void Apply(FretboardProfile profile)
        {
            _profile = profile;
            _geometry = profile?.Geometry ?? default;
            BuildNeckMarks();
        }

        void OnChart(string json)
        {
            _chart = FrettedChart.Parse(json);
            _cursor = 0;
            Debug.Log($"[guitar] chart: role={_chart?.Role ?? "?"} fretted={_chart?.IsFretted ?? false} "
                    + $"notes={_chart?.Notes.Count ?? 0} anchors={(_chart == null ? 0 : _chart.Anchors.Count)}"
                    + (_chart is { AnchorsDerived: true } ? " (derived)" : ""));
        }

        void Update()
        {
            if (_profile == null || anchor == null)
            {
                HideFrom(0, 0, 0, 0, 0);
                SetStaticsVisible(0f, default);
                SetNotTracked(false);
                return;
            }

            // Say so when the guitar has gone out of view: the highway is
            // holding or fading, and the player should know why.
            SetNotTracked(anchor.HasPose && !anchor.IsTracked);

            var visibility = anchor.Visibility;
            Problem = Diagnose();
            var now = link != null ? (float)link.SongTimeSec : 0f;
            var hand = _chart != null && _chart.IsFretted
                ? _chart.AnchorAt(now + bandLeadSeconds)
                : new FrettedChart.Anchor(0f, 1, 4);

            if (visibility <= 0.001f)
            {
                HideFrom(0, 0, 0, 0, 0);
                SetStaticsVisible(0f, hand);
                return;
            }

            UpdatePlayerSide();
            SetStaticsVisible(visibility, hand);

            if (Problem != null)
            {
                HideFrom(0, 0, 0, 0, 0);
                return;
            }

            DrawNotes(now, visibility, hand);
        }

        string Diagnose()
        {
            if (link == null) return "No link to the desktop.";
            if (!link.IsConnected) return "Not connected to the desktop.";
            if (!link.CanChooseInstrument)
            {
                return "This desktop app is too old to send guitar parts. Update AuralPrimer on the PC.";
            }
            if (_chart == null) return "No song loaded on the desktop.";
            if (!_chart.IsFretted)
            {
                return $"This song has no {(_profile.isBass ? "bass" : "guitar")} part with strings and frets.";
            }
            return null;
        }

        /// <summary>
        /// Which side of the neck the player is on, with a margin so a guitar
        /// held flat does not flip the lane back and forth.
        /// </summary>
        void UpdatePlayerSide()
        {
            var head = Camera.main;
            if (head == null) return;
            var local = transform.InverseTransformPoint(head.transform.position);
            var magnitude = local.magnitude;
            if (magnitude < 1e-4f) return;
            if (local.z < -0.2f * magnitude) _playerSide = -1;
            else if (local.z > 0.2f * magnitude) _playerSide = 1;
        }

        void DrawNotes(float now, float visibility, FrettedChart.Anchor hand)
        {
            var notes = _chart.Notes;
            var spacing = link.NoteSpacing.HasValue ? Mathf.Max(0.01f, (float)link.NoteSpacing.Value) : 1f;
            // The desktop's semantics: the lane keeps its length and shows
            // lookAhead / spacing seconds of music.
            var window = lookAheadSeconds / spacing;
            var metresPerSecond = _profile.laneLengthMetres / Mathf.Max(0.05f, window);

            var axis = GuitarAnchor.ToUnity(FretboardGeometry.TimeAxis(_profile.laneTiltDegrees, _playerSide));
            var gemRotation = Quaternion.LookRotation(Vector3.Cross(Vector3.right, axis), axis);

            // Sorted by onset, so a cursor walks with the song; rewound on a seek back.
            while (_cursor > 0 && notes[_cursor - 1].Off >= now - lateGraceSeconds) _cursor--;
            while (_cursor < notes.Count && notes[_cursor].Off < now - lateGraceSeconds) _cursor++;

            _frames.Clear();
            int gems = 0, ribbons = 0, flashes = 0;
            var camera = Camera.main;

            for (var i = _cursor; i < notes.Count && gems < maxGems; i++)
            {
                var note = notes[i];
                if (note.On > now + window) break;
                if (!note.IsPlaced || note.String >= _profile.stringCount) continue;

                var untilOnset = note.On - now;
                var point = ArrivalPoint(note, hand);
                var colour = ForString(note.String);

                // Sustain ribbon: from the gem back along the lane to where the
                // note ends. Once struck it shrinks into the string.
                if (note.Off - note.On >= minimumSustainSeconds && note.Off > now && ribbons < maxGems)
                {
                    var near = Mathf.Max(0f, untilOnset) * metresPerSecond;
                    var far = Mathf.Min(_profile.laneLengthMetres, (note.Off - now) * metresPerSecond);
                    if (far > near + 0.001f)
                    {
                        var ribbon = Rent(_ribbons, ribbons++, "Ribbon");
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

                var gem = Rent(_gems, gems, "Gem");
                gem.transform.localPosition = position;
                gem.transform.localRotation = gemRotation;
                gem.transform.localScale = new Vector3(GemWidth(note, hand), 0.004f, StringGap(point.x) * 0.8f);
                Tint(gem, colour, alpha);

                var label = RentLabel(_labels, gems, "Fret");
                label.text = GemLabel(note);
                label.fontSize = labelSize;
                label.transform.localPosition = position + axis * 0.006f;
                Billboard(label, camera);
                label.color = new Color(1f, 1f, 1f, alpha);
                gems++;

                // The landing, at the real string and fret.
                if (untilOnset <= 0f && untilOnset > -flashSeconds && flashes < maxGems)
                {
                    var flash = Rent(_flashes, flashes++, "Flash");
                    var t = -untilOnset / flashSeconds;
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
                           Mathf.Min(f.minZ, point.z), Mathf.Max(f.maxZ, point.z), distance, f.name ?? note.Chord)
                        : (point.x - half, point.x + half, point.z, point.z, distance, note.Chord);
                    _frames[note.ChordGroup] = frame;
                }
            }

            var bars = 0;
            var names = 0;
            foreach (var frame in _frames.Values)
            {
                if (bars / 4 >= maxChordFrames) break;
                DrawChordFrame(frame, axis, gemRotation, visibility, camera, ref bars, ref names);
            }

            HideFrom(gems, ribbons, flashes, bars, names);
        }

        /// <summary>A rectangle round a chord's gems, with its name beside it.</summary>
        void DrawChordFrame((float minX, float maxX, float minZ, float maxZ, float distance, string name) frame,
                            Vector3 axis, Quaternion rotation, float visibility, Camera camera,
                            ref int bars, ref int names)
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
            var colour = new Color(0.92f, 0.94f, 1f);

            void Bar(Vector3 centre, Vector3 scale, ref int index)
            {
                var r = Rent(_frameBars, index++, "Chord");
                r.transform.localPosition = centre + offset;
                r.transform.localRotation = Quaternion.identity;
                r.transform.localScale = scale;
                Tint(r, colour, alpha);
            }

            Bar(new Vector3(0.5f * (minX + maxX), y, minZ), new Vector3(maxX - minX, bar, bar), ref bars);
            Bar(new Vector3(0.5f * (minX + maxX), y, maxZ), new Vector3(maxX - minX, bar, bar), ref bars);
            Bar(new Vector3(minX, y, 0.5f * (minZ + maxZ)), new Vector3(bar, bar, maxZ - minZ), ref bars);
            Bar(new Vector3(maxX, y, 0.5f * (minZ + maxZ)), new Vector3(bar, bar, maxZ - minZ), ref bars);

            if (string.IsNullOrEmpty(frame.name)) return;
            var label = RentLabel(_chordNames, names++, "Chord name");
            label.text = frame.name;
            label.fontSize = labelSize * 1.2f;
            // Beyond the frame on the player's side, so it reads above the chord.
            var side = _playerSide < 0 ? minZ - 0.012f : maxZ + 0.012f;
            label.transform.localPosition = new Vector3(0.5f * (minX + maxX), y, side) + offset;
            Billboard(label, camera);
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

        // ---- The neck's own marks ------------------------------------------

        /// <summary>
        /// Faint fret wires and strings, and the hand-position band.
        /// </summary>
        /// <remarks>
        /// The wires and strings are already there in the real world; drawn
        /// faintly they show at a glance whether the alignment is right, which
        /// is what the check and its nudges are judged by.
        /// </remarks>
        void BuildNeckMarks()
        {
            foreach (var r in _frets) if (r != null) Destroy(r.gameObject);
            foreach (var r in _strings) if (r != null) Destroy(r.gameObject);
            _frets.Clear();
            _strings.Clear();
            if (_profile == null) return;

            for (var f = 0; f <= _profile.fretCount; f++)
            {
                var r = NewCube($"Fret {f}");
                var x = _geometry.FretX(f);
                r.transform.localPosition = new Vector3(x, 0.0005f, 0f);
                r.transform.localScale = new Vector3(f == 0 ? 0.003f : 0.0012f, 0.0008f, _geometry.SpreadAt(x) + 0.006f);
                _frets.Add(r);
            }

            var end = _geometry.FretX(_profile.fretCount) + 0.02f;
            for (var s = 0; s < _profile.stringCount; s++)
            {
                var r = NewCube($"String {s}");
                var from = new Vector3(0f, 0.002f, _geometry.StringZ(s, 0f));
                var to = new Vector3(end, 0.002f, _geometry.StringZ(s, end));
                r.transform.localPosition = 0.5f * (from + to);
                r.transform.localRotation = Quaternion.FromToRotation(Vector3.right, to - from);
                r.transform.localScale = new Vector3((to - from).magnitude, 0.0008f, 0.0008f);
                _strings.Add(r);
            }

            if (_band == null) _band = NewCube("Hand band");
            if (_notTracked == null)
            {
                _notTracked = NewLabel("Guitar not tracked");
                _notTracked.text = "guitar not tracked";
            }
        }

        void SetStaticsVisible(float visibility, FrettedChart.Anchor hand)
        {
            var show = visibility > 0.001f && _profile != null;
            for (var i = 0; i < _frets.Count; i++)
            {
                _frets[i].gameObject.SetActive(show);
                if (show) Tint(_frets[i], Color.white, (i == 0 ? 0.25f : 0.12f) * visibility);
            }
            for (var s = 0; s < _strings.Count; s++)
            {
                _strings[s].gameObject.SetActive(show);
                if (show) Tint(_strings[s], ForString(s), 0.22f * visibility);
            }

            if (_band == null) return;
            var showBand = show && _chart != null && _chart.IsFretted;
            _band.gameObject.SetActive(showBand);
            if (!showBand) return;

            var from = _geometry.FretX(Mathf.Max(0, hand.Fret - 1));
            var to = _geometry.FretX(Mathf.Min(_profile.fretCount, hand.LastFret));
            var middle = 0.5f * (from + to);
            _band.transform.localPosition = new Vector3(middle, 0.0008f, 0f);
            _band.transform.localRotation = Quaternion.identity;
            _band.transform.localScale = new Vector3(Mathf.Max(0.004f, to - from), 0.0005f, _geometry.SpreadAt(middle) + 0.01f);
            Tint(_band, HandBandColour, 0.10f * visibility);
        }

        void SetNotTracked(bool show)
        {
            if (_notTracked == null) return;
            _notTracked.gameObject.SetActive(show);
            if (!show) return;
            _notTracked.fontSize = labelSize * 1.5f;
            // Just off the nut, on the player's side: where they will look for
            // the neck when it is not there.
            _notTracked.transform.localPosition = new Vector3(0.03f, 0.03f, _playerSide * 0.04f);
            Billboard(_notTracked, Camera.main);
            _notTracked.color = new Color(1f, 0.71f, 0.28f, 0.95f);
        }

        // ---- Pools ------------------------------------------------------------

        Renderer Rent(List<Renderer> pool, int index, string name)
        {
            while (pool.Count <= index) pool.Add(NewCube($"{name} {pool.Count}"));
            var r = pool[index];
            if (!r.gameObject.activeSelf) r.gameObject.SetActive(true);
            return r;
        }

        TextMeshPro RentLabel(List<TextMeshPro> pool, int index, string name)
        {
            while (pool.Count <= index) pool.Add(NewLabel($"{name} {pool.Count}"));
            var t = pool[index];
            if (!t.gameObject.activeSelf) t.gameObject.SetActive(true);
            return t;
        }

        void HideFrom(int gems, int ribbons, int flashes, int bars, int names)
        {
            HideFrom(_gems, gems);
            HideFrom(_labels, gems);
            HideFrom(_ribbons, ribbons);
            HideFrom(_flashes, flashes);
            HideFrom(_frameBars, bars);
            HideFrom(_chordNames, names);
        }

        static void HideFrom<T>(List<T> pool, int from) where T : Component
        {
            for (var i = from; i < pool.Count; i++)
            {
                if (pool[i] != null && pool[i].gameObject.activeSelf) pool[i].gameObject.SetActive(false);
            }
        }

        Renderer NewCube(string name)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = name;
            // A collider here would fight hand tracking for nothing.
            Destroy(go.GetComponent<Collider>());
            go.transform.SetParent(transform, false);
            var r = go.GetComponent<Renderer>();
            r.sharedMaterial = _material;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            r.receiveShadows = false;
            return r;
        }

        TextMeshPro NewLabel(string name)
        {
            var go = new GameObject(name);
            go.transform.SetParent(transform, false);
            var label = go.AddComponent<TextMeshPro>();
            if (labelFont != null) label.font = labelFont;
            label.alignment = TextAlignmentOptions.Center;
            label.textWrappingMode = TextWrappingModes.NoWrap;
            label.fontStyle = FontStyles.Bold;
            label.raycastTarget = false;
            label.rectTransform.sizeDelta = new Vector2(0.08f, 0.02f);
            return label;
        }

        /// <summary>Turn a label to face the eye.</summary>
        /// <remarks>
        /// TMP draws both faces, so a label turned the wrong way is mirrored
        /// rather than hidden. Looking along +Z from the camera is the readable
        /// side. Not yet checked on the headset.
        /// </remarks>
        static void Billboard(TextMeshPro label, Camera camera)
        {
            if (camera == null) return;
            var toLabel = label.transform.position - camera.transform.position;
            if (toLabel.sqrMagnitude < 1e-8f) return;
            label.transform.rotation = Quaternion.LookRotation(toLabel, camera.transform.up);
        }

        void Tint(Renderer renderer, Color colour, float alpha)
        {
            colour.a = Mathf.Clamp01(alpha);
            _block.Clear();
            _block.SetColor(BaseColorId, colour);
            renderer.SetPropertyBlock(_block);
        }

        static Material NewMaterial()
        {
            // Always-included in Graphics Settings, like the keys lane's
            // fallback: a runtime-built material cannot find a stripped shader.
            var shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null) shader = Shader.Find("Sprites/Default");
            var material = new Material(shader);
            if (material.HasProperty("_Surface")) material.SetFloat("_Surface", 1f);
            material.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
            material.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            material.SetInt("_ZWrite", 0);
            material.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
            material.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            return material;
        }

        static Color Hex(int rgb) =>
            new(((rgb >> 16) & 0xff) / 255f, ((rgb >> 8) & 0xff) / 255f, (rgb & 0xff) / 255f);
    }
}
