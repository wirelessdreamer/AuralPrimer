// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Draws the virtual keys over the real keyboard, and lights the ones being
// played. This is what makes calibration verifiable: the app knows which pitch
// arrived, so if the lit key is not the one under the player's finger, the
// error is visible immediately rather than subtly wrong all session.

using System.Collections.Generic;
using AuralPrimer.Link;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed class KeyboardOverlay : MonoBehaviour
    {
        [SerializeField] MrLinkBehaviour link;

        [Tooltip("Depth of a white key marker, in metres (front to back).")]
        [SerializeField] float whiteKeyDepth = 0.14f;

        [Tooltip("Depth of a black key marker. Shorter, as on a real keyboard.")]
        [SerializeField] float blackKeyDepth = 0.09f;

        [Tooltip("How far above the key bed the markers float, to avoid z-fighting "
               + "with the real keys in passthrough.")]
        [SerializeField] float hoverMetres = 0.004f;

        CalibrationProfile _profile;
        KeyboardLayout _layout;
        readonly Dictionary<int, Transform> _keyMarkers = new();
        readonly List<int> _litLastFrame = new();

        [Header("Materials")]
        [Tooltip("Assigned from project assets so the build keeps the shader AND "
               + "the transparent variant. Built at runtime instead, the variant "
               + "is stripped and the markers render as nothing at all.")]
        [SerializeField] Material idleWhiteMaterial;
        [SerializeField] Material idleBlackMaterial;
        [SerializeField] Material litMaterial;

        Material _idleWhite;
        Material _idleBlack;
        Material _lit;

        void Awake() => BuildMaterials();

        /// <summary>Rebuild the overlay for a calibration.</summary>
        public void Apply(CalibrationProfile profile)
        {
            _profile = profile;
            if (profile == null || !profile.IsCalibrated)
            {
                Clear();
                return;
            }

            _layout = profile.BuildLayout();
            Rebuild();
            var head = Camera.main;
            var eye = head != null ? head.transform.position : Vector3.zero;
            var lowest = _keyMarkers.TryGetValue(_layout.LowestPitch, out var lo) ? lo.position : Vector3.zero;
            var highest = _keyMarkers.TryGetValue(_layout.HighestPitch, out var hi) ? hi.position : Vector3.zero;
            Debug.Log($"[overlay] {_keyMarkers.Count} markers | eye={eye} "
                    + $"| lowestKey={lowest} (dHead={lowest - eye}) "
                    + $"| highestKey={highest} (dHead={highest - eye})");
        }

        /// <summary>
        /// When set, keys light from this instead of the live link.
        /// </summary>
        /// <remarks>
        /// Playback needs the same lit keys as the live performance, and
        /// duplicating the lighting logic in the player would let the two drift
        /// until a replay lit different keys than the take it came from.
        /// </remarks>
        public IReadOnlyList<(byte pitch, byte velocity)> PlaybackNotes { get; set; }

        void Update()
        {
            if (_profile == null || _keyMarkers.Count == 0) return;

            var notes = PlaybackNotes;
            if (notes == null)
            {
                if (link == null) return;
                notes = link.HeldNotes;
            }

            // Restore anything lit last frame, then light what is held now. The
            // host sends the full held set, so this needs no note-off tracking:
            // a dropped packet self-corrects on the next one instead of leaving
            // a key stuck on.
            foreach (var pitch in _litLastFrame)
            {
                if (_keyMarkers.TryGetValue(pitch, out var marker) && marker != null)
                {
                    SetMaterial(marker, KeyboardLayout.IsBlack(pitch) ? _idleBlack : _idleWhite);
                }
            }
            _litLastFrame.Clear();

            foreach (var note in notes)
            {
                if (!_keyMarkers.TryGetValue(note.pitch, out var marker) || marker == null) continue;
                SetMaterial(marker, _lit);
                _litLastFrame.Add(note.pitch);
            }
        }

        /// <summary>
        /// Make sure a marker exists for every key, then place them all.
        /// </summary>
        /// <remarks>
        /// Creation is separated from placement because the edge handles move
        /// the calibration every frame while a player drags one. Destroying and
        /// recreating sixty-one objects at that rate is a stutter you can feel,
        /// and it throws away the lit state mid-press.
        /// </remarks>
        void Rebuild()
        {
            var expected = _layout.HighestPitch - _layout.LowestPitch + 1;
            if (_keyMarkers.Count != expected || !_keyMarkers.ContainsKey(_layout.LowestPitch))
            {
                Clear();
                for (var pitch = _layout.LowestPitch; pitch <= _layout.HighestPitch; pitch++)
                {
                    var marker = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    marker.name = $"Key {pitch}";
                    // Colliders would fight hand tracking and the passthrough
                    // scene for no benefit; these are pure visuals.
                    Destroy(marker.GetComponent<Collider>());
                    marker.transform.SetParent(transform, false);
                    SetMaterial(marker.transform,
                                KeyboardLayout.IsBlack(pitch) ? _idleBlack : _idleWhite);
                    _keyMarkers[pitch] = marker.transform;
                }
            }

            Place();
        }

        /// <summary>Put every marker where the current calibration says it goes.</summary>
        void Place()
        {
            // Tell the interactors where the keys ended up, so the hand rays can
            // get out of the way when a hand is over them. Published on every
            // placement rather than once, because an edge drag moves the bed.
            AuralPrimer.UI.KeyboardProximity.Publish(
                transform.TransformPoint(_profile.leftEdge),
                transform.TransformPoint(_profile.rightEdge));

            var right = _profile.RightAxis;
            // Canted: two pinched points fix a line, not a plane, so the roll
            // about that line is the one thing calibration cannot know.
            var up = _profile.CantedUp;
            var forward = Vector3.Cross(right, up).normalized;
            var width = _profile.WidthMetres;

            // Point the keys at the player. Which way the cross product faces
            // depends on which physical edge the user happened to mark first, so
            // half the time the keys extend away from them — the overlay reads as
            // a mirrored keyboard, and pressing a real key lights one that is
            // facing the wrong direction. Decide it from where the player is,
            // rather than from the order the edges were pinched.
            var head = Camera.main;
            if (head != null)
            {
                var centre = Vector3.Lerp(_profile.leftEdge, _profile.rightEdge, 0.5f);
                var centreWorld = transform.TransformPoint(centre);
                var toPlayer = transform.InverseTransformDirection(
                    head.transform.position - centreWorld);
                if (Vector3.Dot(forward, toPlayer) < 0f) forward = -forward;
            }

            for (var pitch = _layout.LowestPitch; pitch <= _layout.HighestPitch; pitch++)
            {
                if (!_keyMarkers.TryGetValue(pitch, out var marker) || marker == null) continue;

                var isBlack = KeyboardLayout.IsBlack(pitch);
                var keyWidth = (float)_layout.NormalisedWidth(pitch) * width;
                var depth = isBlack ? blackKeyDepth : whiteKeyDepth;

                // Black keys stand above the white ones, as they do on the
                // instrument. Drawn at the same height they were coplanar with
                // the wider white plates and simply not visible — the user saw
                // an overlay of white keys only.
                //
                // Depth needs no such correction: both run from the pinched back
                // edge toward the player, so the shorter black key already stops
                // short of the white keys' front edge. Adding a set-back on top
                // re-centred it on the white key instead, which is the one
                // arrangement a keyboard never has.

                // Local, not world: this object is parented to the spatial
                // anchor, so the anchor's transform carries the whole keyboard
                // when the runtime re-localises it.
                marker.localPosition = _profile.KeyPosition(_layout, pitch)
                                     + up * (hoverMetres + (isBlack ? 0.012f : 0f))
                                     + forward * (depth * 0.5f);
                marker.localRotation = Quaternion.LookRotation(forward, up);
                // Deliberately flat: a thin plate reads as an overlay ON the real
                // key rather than a block sitting on top of it.
                // 2 mm at 22% alpha was invisible against a real keyboard in
                // passthrough. Thick enough to read as an object, still flat
                // enough to read as an overlay on the key rather than a block.
                marker.localScale = new Vector3(keyWidth * 0.85f, 0.006f, depth);
            }
        }

        void Clear()
        {
            foreach (var marker in _keyMarkers.Values)
            {
                if (marker != null) Destroy(marker.gameObject);
            }
            _keyMarkers.Clear();
            _litLastFrame.Clear();
            // No keys drawn means no keys to keep the ray off.
            AuralPrimer.UI.KeyboardProximity.Clear();
        }

        void BuildMaterials()
        {
            // Runtime construction is the fallback, not the plan: a material
            // built here can only use shader variants some asset already pulled
            // into the build.
            _idleWhite = idleWhiteMaterial != null
                ? idleWhiteMaterial : NewTransparent(new Color(0.55f, 0.75f, 1f, 0.16f));
            _idleBlack = idleBlackMaterial != null
                ? idleBlackMaterial : NewTransparent(new Color(0.25f, 0.40f, 0.75f, 0.22f));
            // Cyan matches the live-key highlight the 2D client already uses for
            // exactly this, so the two clients read the same way.
            _lit = litMaterial != null
                ? litMaterial : NewTransparent(new Color(0.13f, 0.83f, 0.93f, 0.85f));
        }

        static Material NewTransparent(Color color)
        {
            // URP Lit if present, falling back to Unlit — the client targets URP,
            // but failing to find a shader should dim the overlay, not delete it.
            var shader = Shader.Find("Universal Render Pipeline/Unlit")
                      ?? Shader.Find("Unlit/Color")
                      ?? Shader.Find("Sprites/Default");

            var material = new Material(shader);
            if (material.HasProperty("_BaseColor")) material.SetColor("_BaseColor", color);
            if (material.HasProperty("_Color")) material.SetColor("_Color", color);

            // Transparent blending, set explicitly: URP's Unlit defaults to opaque
            // and would hide the real keyboard rather than overlay it.
            if (material.HasProperty("_Surface")) material.SetFloat("_Surface", 1f);
            material.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
            material.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            material.SetInt("_ZWrite", 0);
            material.renderQueue = (int)UnityEngine.Rendering.RenderQueue.Transparent;
            material.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
            return material;
        }

        static void SetMaterial(Transform marker, Material material)
        {
            if (marker.TryGetComponent<Renderer>(out var renderer))
            {
                renderer.sharedMaterial = material;
            }
        }
    }
}
