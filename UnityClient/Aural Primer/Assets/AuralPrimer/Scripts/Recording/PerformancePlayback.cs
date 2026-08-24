// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Plays a recording back in the room it was made in.
//
// The poses were captured in world space against a spatial anchor that the
// runtime re-localises, so a take replayed in the same room lands on the same
// keyboard: the recorded hands sit over the real keys they were playing. That
// is the reason to store poses rather than video — the performance comes back
// to the place it happened, at any angle you care to stand.
//
// Joints are drawn as plain spheres. A retargeted avatar would look better and
// would also be a second thing that can be wrong; if the spheres are in the
// wrong place the capture is wrong, and nothing about a rig would make that
// clearer.

using System.Collections.Generic;
using UnityEngine;

namespace AuralPrimer.Recording
{
    [AddComponentMenu("AuralPrimer/Performance Playback")]
    public sealed class PerformancePlayback : MonoBehaviour
    {
        [Tooltip("Diameter of a hand joint marker.")]
        [SerializeField] float handJointMetres = 0.012f;

        [Tooltip("Diameter of a body joint marker. Larger: they are further away "
               + "and there are fewer of them.")]
        [SerializeField] float bodyJointMetres = 0.030f;

        [SerializeField] Material handMaterial;
        [SerializeField] Material bodyMaterial;
        [SerializeField] Material headMaterial;

        public bool HasRecording => _reader != null;
        public bool IsPlaying { get; private set; }
        public float Duration => _reader?.Duration ?? 0f;
        public float Time { get; private set; }
        public string LoadedName =>
            _reader != null ? System.IO.Path.GetFileNameWithoutExtension(_reader.Path) : null;

        /// <summary>Notes held at the current playback position.</summary>
        public IReadOnlyList<(byte pitch, byte velocity)> HeldNotes => _held;

        PerformanceReader _reader;
        readonly List<(byte pitch, byte velocity)> _held = new();
        readonly Dictionary<string, Transform[]> _markers = new();
        Transform _root;

        void OnDestroy() => Unload();

        /// <summary>Open a recording and show its first frame.</summary>
        public bool Load(string path)
        {
            Unload();

            _reader = PerformanceReader.Load(path);
            if (_reader == null) return false;

            _root = new GameObject("Playback").transform;
            _root.SetParent(transform, false);

            foreach (var channel in _reader.Info.channels)
            {
                if (channel.kind != "pose") continue;
                _markers[channel.name] = BuildMarkers(channel.name, channel.count);
            }

            Time = 0f;
            IsPlaying = false;
            Apply();
            return true;
        }

        public void Unload()
        {
            if (_root != null) Destroy(_root.gameObject);
            _root = null;
            _markers.Clear();
            _held.Clear();
            _reader = null;
            IsPlaying = false;
            Time = 0f;
        }

        public void Play() { if (_reader != null) IsPlaying = true; }
        public void Pause() => IsPlaying = false;
        public void TogglePlay() { if (IsPlaying) Pause(); else Play(); }

        /// <summary>Jump to a point in the take. Clamped to its length.</summary>
        public void Seek(float seconds)
        {
            if (_reader == null) return;
            Time = Mathf.Clamp(seconds, 0f, _reader.Duration);
            Apply();
        }

        /// <summary>Move by an offset, for skip-back / skip-forward controls.</summary>
        public void Nudge(float seconds) => Seek(Time + seconds);

        void Update()
        {
            if (_reader == null || !IsPlaying) return;

            Time += UnityEngine.Time.deltaTime;
            if (Time >= _reader.Duration)
            {
                // Stop at the end rather than looping: a take that silently
                // restarts looks like a take that never finished.
                Time = _reader.Duration;
                IsPlaying = false;
            }

            Apply();
        }

        void Apply()
        {
            var frame = _reader.Read(_reader.IndexAt(Time));
            if (frame == null) return;

            foreach (var pair in _markers)
            {
                if (!frame.Poses.TryGetValue(pair.Key, out var poses)) continue;

                var markers = pair.Value;
                for (var i = 0; i < markers.Length; i++)
                {
                    var pose = i < poses.Length ? poses[i] : default;

                    // An untracked joint was written as an identity pose, which
                    // would otherwise pile every missing joint at the world
                    // origin — a clump of spheres on the floor that reads as a
                    // bug in playback rather than a gap in the capture.
                    var known = pose.position.sqrMagnitude > 1e-8f;
                    markers[i].gameObject.SetActive(known);
                    if (known) markers[i].SetPositionAndRotation(pose.position, pose.rotation);
                }
            }

            _held.Clear();
            _held.AddRange(frame.Notes);
        }

        Material _fallback;

        /// <summary>
        /// A plain opaque URP material, built once, for when no asset is bound.
        /// </summary>
        /// <remarks>
        /// Second best: a material built at runtime can only use shader variants
        /// that some asset already pulled into the build. The assigned assets are
        /// the real answer; this exists so an unassigned field degrades to a
        /// visible sphere rather than an invisible or magenta one.
        /// </remarks>
        Material Fallback()
        {
            if (_fallback != null) return _fallback;

            var shader = Shader.Find("Universal Render Pipeline/Lit")
                      ?? Shader.Find("Universal Render Pipeline/Unlit")
                      ?? Shader.Find("Sprites/Default");

            _fallback = new Material(shader);
            var colour = new Color(0.20f, 0.90f, 1f);
            if (_fallback.HasProperty("_BaseColor")) _fallback.SetColor("_BaseColor", colour);
            if (_fallback.HasProperty("_Color")) _fallback.SetColor("_Color", colour);
            return _fallback;
        }

        Transform[] BuildMarkers(string channel, int count)
        {
            var parent = new GameObject(channel).transform;
            parent.SetParent(_root, false);

            var size = channel switch
            {
                "leftHand" or "rightHand" => handJointMetres,
                "head" => 0.06f,
                _ => bodyJointMetres,
            };

            var material = channel switch
            {
                "leftHand" or "rightHand" => handMaterial,
                "head" => headMaterial,
                _ => bodyMaterial,
            };

            var markers = new Transform[count];
            for (var i = 0; i < count; i++)
            {
                var marker = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                marker.name = $"{channel} {i}";
                // Pure visuals; a collider here would catch grabs meant for the
                // transport controls floating in the same space.
                Destroy(marker.GetComponent<Collider>());
                marker.transform.SetParent(parent, false);
                marker.transform.localScale = Vector3.one * size;
                if (marker.TryGetComponent<Renderer>(out var renderer))
                {
                    // Never leave the primitive's default material in place. That
                    // is the built-in Standard shader, which a URP build does not
                    // ship — it resolves to magenta, and having no stereo
                    // instancing variant it draws into one eye only.
                    renderer.sharedMaterial = material != null ? material : Fallback();
                }
                markers[i] = marker.transform;
            }
            return markers;
        }
    }
}
