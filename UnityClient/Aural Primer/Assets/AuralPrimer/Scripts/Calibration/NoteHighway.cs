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

                var key = _profile.KeyPosition(_layout, note.Pitch);
                var isBlack = KeyboardLayout.IsBlack(note.Pitch);

                // Distance above the keys is time-until-played. A note being held
                // now has already crossed the keys, so it is clamped there rather
                // than sinking through the keyboard.
                var startHeight = Mathf.Max(0f, note.On - now) * metresPerSecond;
                var endHeight = Mathf.Max(0f, note.Off - now) * metresPerSecond;
                var length = Mathf.Max(minimumNoteLengthMetres, endHeight - startHeight);

                var slab = Rent(used, isBlack);
                slab.position = key + laneUp * (startHeight + length * 0.5f);
                slab.rotation = Quaternion.LookRotation(forward, laneUp);

                var width = (float)_layout.NormalisedWidth(note.Pitch) * _profile.WidthMetres;
                slab.localScale = new Vector3(width * 0.85f, noteThicknessMetres, length);
                used++;
            }

            for (var i = used; i < _pool.Count; i++)
            {
                if (_pool[i] != null) _pool[i].gameObject.SetActive(false);
            }
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
            _whiteMaterial = NewMaterial(new Color(0.42f, 0.78f, 1f, 0.85f));
            _blackMaterial = NewMaterial(new Color(0.72f, 0.55f, 1f, 0.85f));
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
