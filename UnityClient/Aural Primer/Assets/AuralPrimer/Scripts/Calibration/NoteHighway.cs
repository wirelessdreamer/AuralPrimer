// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Notes descending onto the real keyboard.
//
// Each note falls down the column of the key it belongs to and arrives at that
// key at the moment it should be played. That alignment is the whole point of
// doing this in MR rather than on a screen: the player reads the note against
// the actual key their finger is going to, with no mapping step in between.
//
// Time comes from MrLinkBehaviour.SongTimeSec, which is already compensated for
// clock offset, display latency and the host's audio offset — so nothing here
// needs to know about any of that, and there is no headset-side calibration.

using System;
using System.Collections.Generic;
using UnityEngine;
using AuralPrimer.Link;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Note Highway")]
    public sealed class NoteHighway : MonoBehaviour
    {
        [SerializeField] MrLinkBehaviour link;

        [Tooltip("Seconds of music visible between the top of the lane and the keys. "
               + "Lower reads as faster; the note still arrives at the same moment.")]
        [SerializeField] float lookAheadSeconds = 3f;

        [Tooltip("Thickness of a note slab, in metres.")]
        [SerializeField] float noteThicknessMetres = 0.006f;

        [Tooltip("Shortest a note may be drawn, so a staccato note is still visible.")]
        [SerializeField] float minimumNoteLengthMetres = 0.012f;

        [Tooltip("Notes are pooled; this bounds how many exist at once.")]
        [SerializeField] int maxVisibleNotes = 256;

        readonly List<ChartNote> _notes = new();
        readonly List<Transform> _pool = new();
        readonly List<Renderer> _poolRenderers = new();

        CalibrationProfile _profile;
        KeyboardLayout _layout;
        float _nextReportAt;
        Transform _backdrop;
        Transform _hitLine;

        [Header("Materials")]
        [Tooltip("Assigned from project assets so the build keeps the shader AND "
               + "the transparent variant it needs.")]
        [SerializeField] Material noteWhiteMaterial;
        [SerializeField] Material noteBlackMaterial;
        [SerializeField] Material laneBackdropMaterial;
        [SerializeField] Material hitLineMaterial;

        Material _backdropMaterial;
        Material _hitLineMaterial;
        Material _whiteMaterial;
        Material _blackMaterial;
        int _cursor;

        readonly struct ChartNote
        {
            public readonly float On;
            public readonly float Off;
            public readonly int Pitch;

            public ChartNote(float on, float off, int pitch)
            {
                On = on;
                Off = off;
                Pitch = pitch;
            }
        }

        void Awake() => BuildMaterials();

        void OnEnable()
        {
            if (link != null) link.ChartReceived += OnChart;
        }

        void OnDisable()
        {
            if (link != null) link.ChartReceived -= OnChart;
        }

        /// <summary>Point the lane at a calibration. Without one there is no
        /// keyboard to line notes up with, so nothing is drawn.</summary>
        public void Apply(CalibrationProfile profile)
        {
            _profile = profile != null && profile.IsCalibrated ? profile : null;
            _layout = _profile?.BuildLayout();
            _cursor = 0;
            HideAll();
            BuildBackdrop();
        }

        void OnChart(string json)
        {
            _notes.Clear();
            _cursor = 0;

            try
            {
                ParseNotes(json, _notes);
            }
            catch (Exception e)
            {
                Debug.LogError($"[highway] could not read the chart: {e.Message}");
                _notes.Clear();
            }

            Debug.Log($"[highway] chart loaded: {_notes.Count} notes");
        }

        void Update()
        {
            if (_profile == null || link == null || _notes.Count == 0)
            {
                Report($"idle: profile={(_profile != null)} link={(link != null)} notes={_notes.Count}");
                HideAll();
                return;
            }

            var now = (float)link.SongTimeSec;
            var horizon = now + lookAheadSeconds;

            // The chart is sorted by onset, so a cursor walks forward with the
            // song instead of scanning thousands of notes every frame. Rewind it
            // if the player seeks backwards.
            while (_cursor > 0 && _notes[_cursor - 1].Off >= now) _cursor--;
            while (_cursor < _notes.Count && _notes[_cursor].Off < now) _cursor++;

            var right = _profile.RightAxis;
            var up = _profile.up.sqrMagnitude > 1e-6f ? _profile.up.normalized : Vector3.up;
            var forward = Vector3.Cross(right, up).normalized;

            // The lane leans back over the keys rather than rising straight up:
            // vertical, it would be edge-on to a seated player and unreadable.
            var tilt = Quaternion.AngleAxis(_profile.laneTiltDegrees, right);
            var laneUp = tilt * up;

            var metresPerSecond = _profile.laneHeightMetres
                                * Mathf.Max(0.01f, _profile.spacingMultiplier)
                                / Mathf.Max(0.05f, lookAheadSeconds);

            var used = 0;
            for (var i = _cursor; i < _notes.Count && used < maxVisibleNotes; i++)
            {
                var note = _notes[i];
                if (note.On > horizon) break; // sorted, so everything after is further away

                // A chart can range wider than the instrument in front of the
                // player — this one runs to pitch 31 against a keyboard starting
                // at 36. Those notes have no key to fall onto, and placing them
                // by extrapolation would hang them off the end of the keyboard
                // as if they belonged there.
                if (note.Pitch < _layout.LowestPitch || note.Pitch > _layout.HighestPitch) continue;

                var key = _profile.KeyPosition(_layout, note.Pitch);
                var isBlack = KeyboardLayout.IsBlack(note.Pitch);

                // Distance above the keys is time-until-played. A note being held
                // now has already crossed the keys, so it is clamped there rather
                // than sinking through the keyboard.
                var startHeight = Mathf.Max(0f, note.On - now) * metresPerSecond;
                var endHeight = Mathf.Max(0f, note.Off - now) * metresPerSecond;
                var length = Mathf.Max(minimumNoteLengthMetres, endHeight - startHeight);

                var slab = Rent(used, isBlack);
                // Local: this lane is parented to the spatial anchor along with
                // the keys it belongs above.
                slab.localPosition = key + laneUp * (startHeight + length * 0.5f);
                slab.localRotation = Quaternion.LookRotation(forward, laneUp);

                var width = (float)_layout.NormalisedWidth(note.Pitch) * _profile.WidthMetres;
                slab.localScale = new Vector3(width * 0.85f, noteThicknessMetres, length);
                used++;
            }

            for (var i = used; i < _pool.Count; i++)
            {
                if (_pool[i] != null) _pool[i].gameObject.SetActive(false);
            }

            Report($"t={now:F2}s drew={used} cursor={_cursor}/{_notes.Count} "
                 + $"laneOrigin={transform.position} parent={(transform.parent != null ? transform.parent.name : "none")} "
                 + $"firstSlab={(used > 0 ? _pool[0].position.ToString() : "n/a")}");
        }

        /// <summary>Once a second, so the log stays readable at 72 fps.</summary>
        void Report(string message)
        {
            if (Time.unscaledTime < _nextReportAt) return;
            _nextReportAt = Time.unscaledTime + 1f;
            Debug.Log($"[highway] {message}");
        }

        /// <summary>
        /// The lane's own surface, matching the desktop's roll background.
        /// </summary>
        /// <remarks>
        /// Without it the notes hang in mid-air with nothing to read them
        /// against, and in passthrough they compete with whatever is behind the
        /// keyboard. Deliberately translucent rather than opaque: this is mixed
        /// reality, and a solid panel would wall off the room.
        /// </remarks>
        void BuildBackdrop()
        {
            if (_profile == null)
            {
                if (_backdrop != null) _backdrop.gameObject.SetActive(false);
                if (_hitLine != null) _hitLine.gameObject.SetActive(false);
                return;
            }

            if (_backdrop == null) _backdrop = NewSurface("Lane Backdrop", _backdropMaterial);
            if (_hitLine == null) _hitLine = NewSurface("Hit Line", _hitLineMaterial);

            var right = _profile.RightAxis;
            var up = _profile.up.sqrMagnitude > 1e-6f ? _profile.up.normalized : Vector3.up;
            var forward = Vector3.Cross(right, up).normalized;
            var tilt = Quaternion.AngleAxis(_profile.laneTiltDegrees, right);
            var laneUp = tilt * up;

            var width = _profile.WidthMetres;
            var height = _profile.laneHeightMetres * Mathf.Max(0.01f, _profile.spacingMultiplier);
            var centre = Vector3.Lerp(_profile.leftEdge, _profile.rightEdge, 0.5f);

            // Sit a few millimetres behind the notes so they read as being on it.
            const float behind = 0.004f;

            _backdrop.gameObject.SetActive(true);
            _backdrop.localPosition = centre + laneUp * (height * 0.5f) - forward * behind;
            _backdrop.localRotation = Quaternion.LookRotation(forward, laneUp);
            _backdrop.localScale = new Vector3(width, 0.001f, height);

            // The line the note has to be on when you play it — the desktop's
            // "PLAY HERE" band.
            _hitLine.gameObject.SetActive(true);
            _hitLine.localPosition = centre + laneUp * 0.004f - forward * (behind * 0.5f);
            _hitLine.localRotation = Quaternion.LookRotation(forward, laneUp);
            _hitLine.localScale = new Vector3(width, 0.001f, 0.012f);
        }

        Transform NewSurface(string name, Material material)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = name;
            Destroy(go.GetComponent<Collider>());
            go.transform.SetParent(transform, false);
            if (go.TryGetComponent<Renderer>(out var r)) r.sharedMaterial = material;
            return go.transform;
        }

        Transform Rent(int index, bool isBlack)
        {
            while (_pool.Count <= index)
            {
                var slab = GameObject.CreatePrimitive(PrimitiveType.Cube);
                slab.name = $"Note {_pool.Count}";
                // Colliders here would fight hand tracking for no benefit.
                Destroy(slab.GetComponent<Collider>());
                slab.transform.SetParent(transform, false);
                _pool.Add(slab.transform);
                _poolRenderers.Add(slab.GetComponent<Renderer>());
            }

            var t = _pool[index];
            if (!t.gameObject.activeSelf) t.gameObject.SetActive(true);

            var renderer = _poolRenderers[index];
            var wanted = isBlack ? _blackMaterial : _whiteMaterial;
            if (renderer != null && renderer.sharedMaterial != wanted) renderer.sharedMaterial = wanted;

            return t;
        }

        void HideAll()
        {
            foreach (var t in _pool)
            {
                if (t != null && t.gameObject.activeSelf) t.gameObject.SetActive(false);
            }
        }

        void BuildMaterials()
        {
            _whiteMaterial = noteWhiteMaterial != null
                ? noteWhiteMaterial : NewMaterial(new Color(0.42f, 0.78f, 1f, 0.85f));
            _blackMaterial = noteBlackMaterial != null
                ? noteBlackMaterial : NewMaterial(new Color(0.72f, 0.55f, 1f, 0.85f));
            // Matching the desktop roll: deep navy ground, warm hit line.
            _backdropMaterial = laneBackdropMaterial != null
                ? laneBackdropMaterial : NewMaterial(new Color(0.04f, 0.06f, 0.12f, 0.55f));
            _hitLineMaterial = hitLineMaterial != null
                ? hitLineMaterial : NewMaterial(new Color(1f, 0.78f, 0.42f, 0.75f));
        }

        static Material NewMaterial(Color color)
        {
            // Always-included in Graphics Settings; a runtime-built material
            // cannot find a shader the build has stripped.
            var shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null) shader = Shader.Find("Sprites/Default");

            var material = new Material(shader);
            if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", color);
            if (material.HasProperty("_Color")) material.SetColor("_Color", color);
            if (material.HasProperty("_Surface")) material.SetFloat("_Surface", 1f);
            material.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
            material.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            material.SetInt("_ZWrite", 0);
            material.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
            material.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            return material;
        }

        /// <summary>
        /// Pull the note array out of the CHART payload.
        /// </summary>
        /// <remarks>
        /// A hand-rolled scan rather than a JSON library: this is four numeric
        /// fields in a known shape, and JsonUtility cannot deserialise a bare
        /// array without a wrapper type anyway. Notes stay in chart order, which
        /// the protocol guarantees is sorted by onset.
        /// </remarks>
        internal static void ParseNotes(string json, List<ChartNoteData> into)
        {
            into.Clear();
            if (string.IsNullOrEmpty(json)) return;

            var notesAt = json.IndexOf("\"notes\"", StringComparison.Ordinal);
            if (notesAt < 0) return;

            var i = json.IndexOf('[', notesAt);
            if (i < 0) return;

            while (i < json.Length)
            {
                var open = json.IndexOf('{', i);
                if (open < 0) break;
                var close = json.IndexOf('}', open);
                if (close < 0) break;

                var span = json.Substring(open, close - open + 1);
                if (TryNumber(span, "on", out var on) &&
                    TryNumber(span, "off", out var off) &&
                    TryNumber(span, "pitch", out var pitch))
                {
                    into.Add(new ChartNoteData((float)on, (float)off, Mathf.RoundToInt((float)pitch)));
                }

                i = close + 1;
            }
        }

        internal readonly struct ChartNoteData
        {
            public readonly float On;
            public readonly float Off;
            public readonly int Pitch;

            public ChartNoteData(float on, float off, int pitch)
            {
                On = on;
                Off = off;
                Pitch = pitch;
            }
        }

        static bool TryNumber(string span, string key, out double value)
        {
            value = 0;
            var marker = $"\"{key}\":";
            var at = span.IndexOf(marker, StringComparison.Ordinal);
            if (at < 0) return false;

            at += marker.Length;
            var end = at;
            while (end < span.Length && (char.IsDigit(span[end]) || span[end] == '-'
                                      || span[end] == '+' || span[end] == '.'
                                      || span[end] == 'e' || span[end] == 'E'))
            {
                end++;
            }

            return double.TryParse(span.Substring(at, end - at),
                                   System.Globalization.NumberStyles.Float,
                                   System.Globalization.CultureInfo.InvariantCulture,
                                   out value);
        }

        void ParseNotes(string json, List<ChartNote> into)
        {
            var scratch = new List<ChartNoteData>();
            ParseNotes(json, scratch);
            into.Clear();
            foreach (var n in scratch) into.Add(new ChartNote(n.On, n.Off, n.Pitch));
        }
    }
}
