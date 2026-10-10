// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The guitar views, picked in the headset: the desktop mock-ups'
// (docs/guitar-prototype/guitar-surfaces.html, nashville-fretboard.html) on a
// real neck.
//
//   Neck highway   notes fly in along a lane and land on their fret   (.Highway.cs)
//   Neck coach     dots on the real strings; next notes count down     (.Neck.cs)
//   Nashville neck numbers by pentatonic position, on the real neck   (.Neck.cs)
//   String lanes   a floating board of falling lanes                   (.Panels.cs)
//   Tab scroll     a floating tab, high e on top                       (.Panels.cs)
//
// Parented to GuitarAnchor, so this transform's local frame IS the neck
// frame: X along the neck from the nut, Y out of the fretboard, Z from the
// lowest string to the highest (FretboardGeometry). The two floating views
// draw on a board of their own near the head end instead.
//
// Built for the Quest 3S / Pro floor: everything is pooled cubes, textured
// quads and labels, one material each, nothing allocated per frame. And it
// stays faint near the neck. Passthrough has no depth here, so anything drawn
// at the strings is drawn OVER the fretting hand.
//
// Labels go through pluggable functions (FretLabel, NoteLookFor), so a later
// view can swap what a dot says without a new renderer.

using System;
using System.Collections.Generic;
using System.Globalization;
using AuralPrimer.Link;
using TMPro;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    /// <summary>Which guitar view is drawn. Stored by number: append, never reorder.</summary>
    public enum GuitarView
    {
        Highway = 0,
        Coach = 1,
        Nashville = 2,
        Lanes = 3,
        Tab = 4,
    }

    [AddComponentMenu("AuralPrimer/Guitar Views")]
    public sealed partial class GuitarViews : MonoBehaviour
    {
        [SerializeField] MrLinkBehaviour link;
        [SerializeField] GuitarAnchor anchor;

        [Tooltip("Font for labels; the TMP default when unset.")]
        [SerializeField] TMP_FontAsset labelFont;

        [Tooltip("Height of the landing point above the fretboard: just over the strings.")]
        [SerializeField] float liftMetres = 0.006f;

        [Tooltip("How long a note sits on its fret after its onset before it goes.")]
        [SerializeField] float lateGraceSeconds = 0.08f;

        [Tooltip("TMP font size of a gem's label. World-space TMP sizes are small numbers; "
               + "tune on the headset.")]
        [SerializeField] float labelSize = 0.06f;

        [Tooltip("Opacity of the dots drawn on the real neck, before the bleed. Kept "
               + "below one: they sit where the fingers go.")]
        [SerializeField, Range(0f, 1f)] float neckDotAlpha = 0.65f;

        /// <summary>The mock-up's accent, for the hand-position band and box outlines.</summary>
        static readonly Color Accent = Hex(0x22d3ee);
        static readonly Color Ink = Hex(0x061018);
        static readonly Color Paper = new(0.94f, 0.95f, 1f);

        public static Color ForString(int s) => Hex(FretLooks.StringColour(s));

        // ---- What is shown ----------------------------------------------------

        public GuitarView View { get; private set; } = GuitarView.Highway;
        public FretLabelMode Labels { get; private set; } = FretLabelMode.Frets;
        public ChordLabelMode Chords { get; private set; } = ChordLabelMode.Numbers;
        public CountFrom Count { get; private set; } = CountFrom.Song;

        /// <summary>Pick a view and how notes are labelled; applies from the next frame.</summary>
        public void SetDisplay(GuitarView view, FretLabelMode labels, ChordLabelMode chords, CountFrom count)
        {
            if (view != View) _viewChanged = true;
            View = view;
            Labels = labels;
            Chords = chords;
            Count = count;
            RebuildKey();
        }

        /// <summary>
        /// What a note says when labels are frets. The fret number by default.
        /// </summary>
        public Func<FrettedChart.Note, string> FretLabel { get; set; } = note => FretText(note.Fret);

        /// <summary>
        /// How a chart note looks on every view: label, colour, shape and rings.
        /// </summary>
        /// <remarks>
        /// Pluggable on purpose, like the desktop's noteLabel: by default the
        /// fret or the Nashville number from the song's key, per the menu.
        /// </remarks>
        public Func<FrettedChart.Note, NoteLook> NoteLookFor { get; set; }

        /// <summary>Why no part is being drawn, for the menu to say; null when one is.</summary>
        public string Problem { get; private set; }

        /// <summary>Something worth saying that does not stop the drawing, such as a missing key.</summary>
        public string Notice { get; private set; }

        // ---- State ----------------------------------------------------------------

        FretboardProfile _profile;
        FretboardGeometry _geometry;
        FrettedChart _chart;
        int[] _open = Array.Empty<int>();
        /// <summary>Each chord group's triad, by group id; null for a group with no clear chord.</summary>
        readonly List<Triad?> _groupTriads = new();
        /// <summary>The song's key as counted, or null when the song has none.</summary>
        NumberFrame? _keyFrame;
        /// <summary>The key frame while labels are numbers: everything that labels by number reads this.</summary>
        NumberFrame? _numbers;
        int _cursor;
        bool _viewChanged;
        /// <summary>Which side of the neck the player looks from: -1 toward the lowest string.</summary>
        int _playerSide = -1;

        Material _material;
        Material _spriteMaterial;
        MaterialPropertyBlock _block;
        static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");
        static readonly int BaseMapId = Shader.PropertyToID("_BaseMap");

        Pool<Renderer> _cubes;
        Pool<Renderer> _sprites;
        Pool<TextMeshPro> _labels;
        readonly List<Renderer> _frets = new();
        readonly List<Renderer> _strings = new();
        Renderer _band;
        TextMeshPro _notTracked;
        TextMeshPro _notice;

        /// <summary>Glyphs the label font lacks, spelled the way players type them.</summary>
        bool _fontHasAccidentals = true;
        readonly Dictionary<string, string> _spelled = new();

        public void Bind(MrLinkBehaviour mrLink, GuitarAnchor neckAnchor)
        {
            if (link != null) link.ChartReceived -= OnChart;
            link = mrLink;
            anchor = neckAnchor;
            if (isActiveAndEnabled && link != null) link.ChartReceived += OnChart;
        }

        void Awake()
        {
            _material = NewMaterial(cullOff: false);
            _spriteMaterial = NewMaterial(cullOff: true);
            _block = new MaterialPropertyBlock();
            _cubes = new Pool<Renderer>(() => NewCube("Mark", transform));
            _sprites = new Pool<Renderer>(() => NewSprite("Dot", transform));
            _labels = new Pool<TextMeshPro>(() => NewLabel("Label", transform));
            NoteLookFor ??= DefaultLook;
        }

        void OnEnable()
        {
            if (link != null) link.ChartReceived += OnChart;
        }

        void OnDisable()
        {
            if (link != null) link.ChartReceived -= OnChart;
            if (_panel != null) _panel.gameObject.SetActive(false);
        }

        void OnDestroy()
        {
            if (_panel != null) Destroy(_panel.gameObject);
        }

        /// <summary>Draw on this neck. Null hides everything.</summary>
        public void Apply(FretboardProfile profile)
        {
            _profile = profile;
            _geometry = profile?.Geometry ?? default;
            BuildNeckMarks();
            RebuildKey();
        }

        void OnChart(string json)
        {
            _chart = FrettedChart.Parse(json);
            _cursor = 0;
            _open = _chart != null && _chart.Tuning.Length > 0 ? _chart.Tuning : Array.Empty<int>();
            _groupTriads.Clear();
            if (_chart != null) BuildChordTriads(_chart);
            RebuildKey();
            Debug.Log($"[guitar] chart: role={_chart?.Role ?? "?"} fretted={_chart?.IsFretted ?? false} "
                    + $"notes={_chart?.Notes.Count ?? 0} anchors={(_chart == null ? 0 : _chart.Anchors.Count)}"
                    + (_chart is { AnchorsDerived: true } ? " (derived)" : "")
                    + (_chart is { HasKey: true } ? $" key={_chart.KeyPitchClass}{(_chart.KeyMinor ? "m" : "")}" : " no key"));
        }

        /// <summary>A triad for every chord group, read from its pitches once per chart.</summary>
        void BuildChordTriads(FrettedChart chart)
        {
            var pitches = new List<int>();
            var group = -1;
            foreach (var note in chart.Notes)
            {
                if (note.ChordGroup != group)
                {
                    if (group >= 0) SetTriad(group, FretNumbers.TriadOf(pitches));
                    group = note.ChordGroup;
                    pitches.Clear();
                }
                if (group >= 0) pitches.Add(note.Pitch);
            }
            if (group >= 0) SetTriad(group, FretNumbers.TriadOf(pitches));
        }

        void SetTriad(int group, Triad? triad)
        {
            while (_groupTriads.Count <= group) _groupTriads.Add(null);
            _groupTriads[group] = triad;
        }

        Triad? TriadFor(FrettedChart.Note note) =>
            note.ChordGroup >= 0 && note.ChordGroup < _groupTriads.Count ? _groupTriads[note.ChordGroup] : null;

        /// <summary>
        /// Work out the key frame and everything that hangs off it, once per
        /// chart, key or setting rather than per frame.
        /// </summary>
        void RebuildKey()
        {
            _keyFrame = _chart is { HasKey: true }
                ? FretNumbers.Frame(new SongKey(_chart.KeyPitchClass, _chart.KeyMinor), Count)
                : null;
            _numbers = Labels == FretLabelMode.Numbers ? _keyFrame : null;
            RebuildNashville();
        }

        // ---- Per frame ------------------------------------------------------------

        void Update()
        {
            if (_profile == null || anchor == null)
            {
                HideViews();
                SetStaticsVisible(0f, default, false);
                SetNotTracked(false);
                SetNotice(null);
                return;
            }

            // Say so when the guitar has gone out of view: the views are
            // holding or fading, and the player should know why.
            SetNotTracked(anchor.HasPose && !anchor.IsTracked);

            var visibility = anchor.Visibility;
            Problem = Diagnose();
            Notice = Problem == null ? NoticeText() : null;
            SetNotice(Notice);

            var now = link != null ? (float)link.SongTimeSec : 0f;
            var playing = Problem == null;
            var hand = playing ? _chart.AnchorAt(now + 0.2f) : new FrettedChart.Anchor(0f, 1, 4);

            if (visibility <= 0.001f)
            {
                HideViews();
                SetStaticsVisible(0f, hand, false);
                return;
            }

            UpdatePlayerSide();
            // The Nashville neck draws its boxes instead of the hand band.
            var showBand = playing && !(View == GuitarView.Nashville && _keyFrame.HasValue && _handBoxes.Count > 0);
            SetStaticsVisible(visibility, hand, showBand);

            if (!playing)
            {
                HideViews();
                return;
            }

            if (_viewChanged)
            {
                // Hide what the last view left up, and let a panel snap to its
                // place rather than glide in from wherever it was.
                HideViews();
                _panelPlaced = false;
                _viewChanged = false;
            }

            AdvanceCursor(now);
            _cubes.Begin();
            _sprites.Begin();
            _labels.Begin();
            BeginPanelFrame();

            switch (View)
            {
                case GuitarView.Highway: DrawHighway(now, visibility, hand); break;
                case GuitarView.Coach: DrawCoach(now, visibility, hand); break;
                case GuitarView.Nashville:
                    // No key, no numbers or boxes: the coach, which needs
                    // neither, draws instead, and the notice says why.
                    if (_keyFrame.HasValue && _handBoxes.Count > 0) DrawNashville(now, visibility);
                    else DrawCoach(now, visibility, hand);
                    break;
                case GuitarView.Lanes: DrawLanes(now, visibility); break;
                case GuitarView.Tab: DrawTab(now, visibility); break;
            }

            _cubes.End();
            _sprites.End();
            _labels.End();
            EndPanelFrame(View == GuitarView.Lanes || View == GuitarView.Tab);
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

        /// <summary>Said plainly when numbers were asked for and the song has no key to count from.</summary>
        string NoticeText()
        {
            var wantsNumbers = Labels == FretLabelMode.Numbers || View == GuitarView.Nashville;
            if (!wantsNumbers) return null;
            if (!_chart.HasKey) return "No key for this song, so no numbers: showing frets.";
            if (_chart.KeyInferred)
            {
                return $"Key {KeyName(_chart.KeyPitchClass, _chart.KeyMinor)}: a guess from the notes.";
            }
            return null;
        }

        static string KeyName(int pitchClass, bool minor) =>
            FretLooks.NoteNames[((pitchClass % 12) + 12) % 12] + (minor ? " minor" : " major");

        void AdvanceCursor(float now)
        {
            var notes = _chart.Notes;
            // Sorted by onset, so a cursor walks with the song; rewound on a seek back.
            while (_cursor > 0 && notes[_cursor - 1].Off >= now - 1f) _cursor--;
            while (_cursor < notes.Count && notes[_cursor].Off < now - 1f) _cursor++;
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

        float Spacing() =>
            link != null && link.NoteSpacing.HasValue ? Mathf.Max(0.01f, (float)link.NoteSpacing.Value) : 1f;

        // ---- Looks ------------------------------------------------------------------

        /// <summary>The menu's choice: by fret and string, or by number from the key.</summary>
        NoteLook DefaultLook(FrettedChart.Note note) =>
            FretLooks.ForNote(_numbers, LowestOpen, note.String, note.Fret, note.Pitch, note.IsMuted,
                              TriadFor(note), Chords, FretLabel(note));

        int LowestOpen => _open.Length > 0 ? _open[0] : 40;

        /// <summary>A chord's name, or its Nashville number when labels are numbers.</summary>
        string ChordTitle(FrettedChart.Note note)
        {
            var triad = TriadFor(note);
            return _numbers.HasValue && triad.HasValue
                ? FretNumbers.ChordNumber(_numbers.Value, triad.Value)
                : note.Chord;
        }

        static readonly string[] FretTexts = BuildFretTexts();

        static string[] BuildFretTexts()
        {
            var texts = new string[37];
            for (var i = 0; i < texts.Length; i++) texts[i] = i.ToString(CultureInfo.InvariantCulture);
            return texts;
        }

        /// <summary>A fret number as text, without allocating for the frets a neck has.</summary>
        static string FretText(int fret) =>
            fret >= 0 && fret < FretTexts.Length ? FretTexts[fret] : fret.ToString(CultureInfo.InvariantCulture);

        /// <summary>
        /// A label as the font can draw it.
        /// </summary>
        /// <remarks>
        /// The numbers are spelled with ♭ and ♯, exactly as the desktop's are.
        /// A font without those glyphs would draw boxes, so they become b and #
        /// there -- the spelling players type when the real symbol is not to hand.
        /// </remarks>
        string Spell(string text)
        {
            if (_fontHasAccidentals || string.IsNullOrEmpty(text)) return text;
            if (_spelled.TryGetValue(text, out var spelled)) return spelled;
            spelled = text.Replace('♭', 'b').Replace('♯', '#');
            _spelled[text] = spelled;
            return spelled;
        }

        // ---- The neck's own marks ------------------------------------------------

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
                var r = NewCube($"Fret {f}", transform);
                var x = _geometry.FretX(f);
                r.transform.localPosition = new Vector3(x, 0.0005f, 0f);
                r.transform.localScale = new Vector3(f == 0 ? 0.003f : 0.0012f, 0.0008f, _geometry.SpreadAt(x) + 0.006f);
                _frets.Add(r);
            }

            var end = _geometry.FretX(_profile.fretCount) + 0.02f;
            for (var s = 0; s < _profile.stringCount; s++)
            {
                var r = NewCube($"String {s}", transform);
                var from = new Vector3(0f, 0.002f, _geometry.StringZ(s, 0f));
                var to = new Vector3(end, 0.002f, _geometry.StringZ(s, end));
                r.transform.localPosition = 0.5f * (from + to);
                r.transform.localRotation = Quaternion.FromToRotation(Vector3.right, to - from);
                r.transform.localScale = new Vector3((to - from).magnitude, 0.0008f, 0.0008f);
                _strings.Add(r);
            }

            if (_band == null) _band = NewCube("Hand band", transform);
            if (_notTracked == null)
            {
                _notTracked = NewLabel("Guitar not tracked", transform);
                _notTracked.text = "guitar not tracked";
            }
            if (_notice == null) _notice = NewLabel("Notice", transform);
        }

        void SetStaticsVisible(float visibility, FrettedChart.Anchor hand, bool showBand)
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
            showBand &= show;
            _band.gameObject.SetActive(showBand);
            if (!showBand) return;

            var from = _geometry.FretX(Mathf.Max(0, hand.Fret - 1));
            var to = _geometry.FretX(Mathf.Min(_profile.fretCount, hand.LastFret));
            var middle = 0.5f * (from + to);
            _band.transform.localPosition = new Vector3(middle, 0.0008f, 0f);
            _band.transform.localRotation = Quaternion.identity;
            _band.transform.localScale = new Vector3(Mathf.Max(0.004f, to - from), 0.0005f, _geometry.SpreadAt(middle) + 0.01f);
            Tint(_band, Accent, 0.10f * visibility);
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
            Billboard(_notTracked.transform, Camera.main);
            _notTracked.color = new Color(1f, 0.71f, 0.28f, 0.95f);
        }

        void SetNotice(string text)
        {
            if (_notice == null) return;
            var show = !string.IsNullOrEmpty(text) && anchor != null && anchor.Visibility > 0.001f;
            _notice.gameObject.SetActive(show);
            if (!show) return;
            _notice.text = text;
            _notice.fontSize = labelSize * 0.9f;
            _notice.rectTransform.sizeDelta = new Vector2(0.3f, 0.03f);
            _notice.transform.localPosition = new Vector3(-0.06f, 0.05f, _playerSide * 0.05f);
            Billboard(_notice.transform, Camera.main);
            _notice.color = new Color(1f, 0.71f, 0.28f, 0.9f);
        }

        void HideViews()
        {
            _cubes?.HideAll();
            _sprites?.HideAll();
            _labels?.HideAll();
            HidePanel();
        }

        // ---- Drawing helpers ---------------------------------------------------------

        /// <summary>Where a dot for string s, fret f sits on the neck. Open strings sit just behind the nut.</summary>
        Vector3 NeckPoint(int s, int fret)
        {
            var x = fret > 0 ? _geometry.FretCentreX(fret) : -0.010f;
            return new Vector3(x, liftMetres, _geometry.StringZ(s, x));
        }

        /// <summary>A dot's radius at a fret: as big as the gap between strings and frets allows.</summary>
        float DotRadius(int fret)
        {
            var gap = _profile.stringCount > 1 ? _geometry.SpreadAt(_geometry.FretCentreX(Mathf.Max(1, fret))) / (_profile.stringCount - 1) : 0.008f;
            var f = Mathf.Max(1, fret);
            var cell = _geometry.FretX(f) - _geometry.FretX(f - 1);
            return Mathf.Clamp(Mathf.Min(gap * 0.45f, cell * 0.38f), 0.0025f, 0.007f);
        }

        /// <summary>
        /// A dot in the mock-up's style: filled, hollow or dashed, a white ring
        /// on the 1, a chord-tone ring outside that, the label on top.
        /// </summary>
        /// <param name="toward">How far toward the eye the whole dot sits, so a lit dot drawn over a
        /// resting one at the same spot is reliably in front of it.</param>
        void DrawDot(Vector3 position, float radius, NoteLook look, float alpha, Camera camera,
                     bool glow = false, float toward = 0f)
        {
            var colour = Hex(look.Colour);
            if (glow)
            {
                // A soft halo behind a sounding note.
                Sprite(position, radius * 2.6f, DotTexture(DotKind.Disc), colour, 0.25f * alpha, camera, toward - 0.0006f);
            }
            if (look.Shape == DotShape.Fill)
            {
                Sprite(position, radius * 2f, DotTexture(DotKind.Disc), colour, alpha, camera, toward);
            }
            else
            {
                Sprite(position, radius * 2f, DotTexture(DotKind.Disc), Ink, 0.85f * alpha, camera, toward);
                Sprite(position, radius * 2f, DotTexture(look.Shape == DotShape.Dashed ? DotKind.Dashed : DotKind.Ring),
                       colour, alpha, camera, toward + 0.0002f);
            }
            if (look.WhiteRing)
            {
                Sprite(position, radius * 2.35f, DotTexture(DotKind.Ring), Paper, alpha, camera, toward + 0.0003f);
            }
            if (look.ToneRing != 0)
            {
                Sprite(position, radius * 2.8f, DotTexture(DotKind.Ring), Hex(look.ToneRing), alpha, camera, toward + 0.0003f);
            }
            if (!string.IsNullOrEmpty(look.Label))
            {
                var label = _labels.Next();
                label.text = Spell(look.Label);
                label.fontSize = radius * (look.Label.Length > 1 ? 9f : 11f);
                label.transform.localPosition = TowardCamera(position, camera, toward + 0.0008f);
                Billboard(label.transform, camera);
                var ink = look.Shape == DotShape.Fill ? Ink : colour;
                ink.a = alpha;
                label.color = ink;
            }
        }

        /// <summary>A closing ring: <paramref name="progress"/> 0 to 1 as the note arrives.</summary>
        void CountdownRing(Vector3 position, float radius, float progress, Color colour, float alpha, Camera camera)
        {
            var step = Mathf.Clamp(Mathf.RoundToInt(progress * ArcSteps), 1, ArcSteps);
            Sprite(position, radius * 2.5f, ArcTexture(step), colour, alpha, camera, 0.0004f);
        }

        /// <summary>A textured quad facing the eye. <paramref name="towardEye"/> settles which of two overlaps is in front.</summary>
        void Sprite(Vector3 localPosition, float size, Texture2D texture, Color colour, float alpha, Camera camera, float towardEye)
        {
            var r = _sprites.Next();
            r.transform.localPosition = towardEye != 0f ? TowardCamera(localPosition, camera, towardEye) : localPosition;
            Billboard(r.transform, camera);
            r.transform.localScale = new Vector3(size, size, 1f);
            colour.a = Mathf.Clamp01(alpha);
            _block.Clear();
            _block.SetColor(BaseColorId, colour);
            _block.SetTexture(BaseMapId, texture);
            r.SetPropertyBlock(_block);
        }

        /// <summary>A local point nudged toward the eye, so overlapping layers keep their order.</summary>
        Vector3 TowardCamera(Vector3 localPosition, Camera camera, float metres)
        {
            if (camera == null) return localPosition;
            var world = transform.TransformPoint(localPosition);
            var toEye = (camera.transform.position - world).normalized;
            return transform.InverseTransformPoint(world + toEye * metres);
        }

        /// <summary>Turn something to face the eye.</summary>
        /// <remarks>
        /// TMP draws both faces, so a label turned the wrong way is mirrored
        /// rather than hidden. Looking along +Z from the camera is the readable
        /// side. Not yet checked on the headset.
        /// </remarks>
        static void Billboard(Transform t, Camera camera)
        {
            if (camera == null) return;
            var toThing = t.position - camera.transform.position;
            if (toThing.sqrMagnitude < 1e-8f) return;
            t.rotation = Quaternion.LookRotation(toThing, camera.transform.up);
        }

        void Tint(Renderer renderer, Color colour, float alpha)
        {
            colour.a = Mathf.Clamp01(alpha);
            _block.Clear();
            _block.SetColor(BaseColorId, colour);
            renderer.SetPropertyBlock(_block);
        }

        // ---- Pools and objects ---------------------------------------------------------

        /// <summary>
        /// Objects handed out in order each frame and hidden when unused.
        /// </summary>
        /// <remarks>
        /// One pool per kind and parent, shared by every view: only one view
        /// draws at a time, so the busiest one sets the size, and nothing is
        /// created or destroyed while the song plays.
        /// </remarks>
        sealed class Pool<T> where T : Component
        {
            readonly List<T> _items = new();
            readonly Func<T> _make;
            int _used;

            public Pool(Func<T> make) => _make = make;

            public void Begin() => _used = 0;

            public T Next()
            {
                if (_used == _items.Count) _items.Add(_make());
                var item = _items[_used++];
                if (!item.gameObject.activeSelf) item.gameObject.SetActive(true);
                return item;
            }

            public void End()
            {
                for (var i = _used; i < _items.Count; i++)
                {
                    if (_items[i] != null && _items[i].gameObject.activeSelf) _items[i].gameObject.SetActive(false);
                }
            }

            public void HideAll()
            {
                _used = 0;
                End();
            }
        }

        Renderer NewCube(string name, Transform parent)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = name;
            // A collider here would fight hand tracking for nothing.
            Destroy(go.GetComponent<Collider>());
            go.transform.SetParent(parent, false);
            var r = go.GetComponent<Renderer>();
            r.sharedMaterial = _material;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            r.receiveShadows = false;
            return r;
        }

        Renderer NewSprite(string name, Transform parent)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
            go.name = name;
            Destroy(go.GetComponent<Collider>());
            go.transform.SetParent(parent, false);
            var r = go.GetComponent<Renderer>();
            r.sharedMaterial = _spriteMaterial;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            r.receiveShadows = false;
            return r;
        }

        TextMeshPro NewLabel(string name, Transform parent)
        {
            var go = new GameObject(name);
            go.transform.SetParent(parent, false);
            var label = go.AddComponent<TextMeshPro>();
            if (labelFont != null) label.font = labelFont;
            label.alignment = TextAlignmentOptions.Center;
            label.textWrappingMode = TextWrappingModes.NoWrap;
            label.fontStyle = FontStyles.Bold;
            label.raycastTarget = false;
            label.rectTransform.sizeDelta = new Vector2(0.08f, 0.02f);
            if (label.font != null && !label.font.HasCharacter('♭', true, true)) _fontHasAccidentals = false;
            return label;
        }

        static Material NewMaterial(bool cullOff)
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
            if (cullOff && material.HasProperty("_Cull")) material.SetFloat("_Cull", (float)UnityEngine.Rendering.CullMode.Off);
            material.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
            material.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            return material;
        }

        // ---- Dot textures ----------------------------------------------------------------
        // Drawn once, at startup, rather than shipped as assets: four small
        // greyscale masks the material tints, and the countdown arc in steps.

        enum DotKind
        {
            Disc,
            Ring,
            Dashed,
        }

        const int DotTextureSize = 64;
        const int ArcSteps = 16;

        static Texture2D[] _dotTextures;
        static Texture2D[] _arcTextures;

        static Texture2D DotTexture(DotKind kind)
        {
            _dotTextures ??= new[]
            {
                MakeMask((r, a) => r <= 0.94f ? 1f : Edge(r, 0.94f, 1f)),
                MakeMask((r, a) => Annulus(r, 0.72f, 0.96f)),
                MakeMask((r, a) => Annulus(r, 0.72f, 0.96f) * (Mathf.Repeat(a / (Mathf.PI * 2f) * 12f, 1f) < 0.6f ? 1f : 0f)),
            };
            return _dotTextures[(int)kind];
        }

        static Texture2D ArcTexture(int step)
        {
            if (_arcTextures == null)
            {
                _arcTextures = new Texture2D[ArcSteps + 1];
                for (var k = 1; k <= ArcSteps; k++)
                {
                    var sweep = k / (float)ArcSteps;
                    _arcTextures[k] = MakeMask((r, a) => a / (Mathf.PI * 2f) <= sweep ? Annulus(r, 0.78f, 0.98f) : 0f);
                }
            }
            return _arcTextures[Mathf.Clamp(step, 1, ArcSteps)];
        }

        static float Edge(float r, float inner, float outer) => Mathf.Clamp01(1f - (r - inner) / (outer - inner));

        static float Annulus(float r, float inner, float outer)
        {
            const float soft = 0.05f;
            return Mathf.Clamp01((r - inner) / soft + 1f) * Mathf.Clamp01((outer - r) / soft + 1f)
                 * (r >= inner - soft && r <= outer + soft ? 1f : 0f);
        }

        /// <summary>
        /// A white mask with alpha from <paramref name="shape"/>(radius 0..1,
        /// angle clockwise from the top, 0..2π).
        /// </summary>
        static Texture2D MakeMask(Func<float, float, float> shape)
        {
            var texture = new Texture2D(DotTextureSize, DotTextureSize, TextureFormat.RGBA32, false)
            {
                wrapMode = TextureWrapMode.Clamp,
                filterMode = FilterMode.Bilinear,
            };
            var pixels = new Color32[DotTextureSize * DotTextureSize];
            for (var y = 0; y < DotTextureSize; y++)
            {
                for (var x = 0; x < DotTextureSize; x++)
                {
                    var u = (x + 0.5f) / DotTextureSize * 2f - 1f;
                    var v = (y + 0.5f) / DotTextureSize * 2f - 1f;
                    var r = Mathf.Sqrt(u * u + v * v);
                    var angle = Mathf.Atan2(u, v); // 0 at the top, clockwise
                    if (angle < 0f) angle += Mathf.PI * 2f;
                    var alpha = r > 1f ? 0f : Mathf.Clamp01(shape(r, angle));
                    pixels[y * DotTextureSize + x] = new Color32(255, 255, 255, (byte)(alpha * 255f));
                }
            }
            texture.SetPixels32(pixels);
            texture.Apply(false, true);
            return texture;
        }

        static Color Hex(uint rgb) =>
            new(((rgb >> 16) & 0xff) / 255f, ((rgb >> 8) & 0xff) / 255f, (rgb & 0xff) / 255f);
    }
}
