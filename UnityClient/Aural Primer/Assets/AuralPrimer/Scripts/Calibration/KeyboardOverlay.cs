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

        [Tooltip("What fraction of its authored opacity an unlit key keeps once the "
               + "keyboard is calibrated. A fraction rather than a flat value, so the "
               + "black keys keep the extra strength their material asks for — they "
               + "are darker and narrower, and fade out first.")]
        [SerializeField, Range(0f, 1f)] float restingOpacity = 0.35f;

        CalibrationProfile _profile;
        KeyboardLayout _layout;
        readonly Dictionary<int, Transform> _keyMarkers = new();
        readonly List<int> _litLastFrame = new();
        readonly Dictionary<int, Renderer> _previewFills = new();
        MaterialPropertyBlock _previewBlock;
        static readonly int BaseColorId = Shader.PropertyToID("_BaseColor");

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
        Material _restingWhite;
        Material _restingBlack;
        Material _next;
        Material _held;

        /// <summary>Colour of a key at the far edge of the preview window.</summary>
        /// <remarks>
        /// The near end is the lit colour on purpose: a preview that converges
        /// on it means "about to be played" and "being played" meet as the same
        /// hue at the moment the note lands, so the key does not jump colour at
        /// the instant the player is watching it hardest.
        ///
        /// It is also opaque, which is what makes a highlight on a black key
        /// readable. Black keys are drawn 12 mm above the white ones, but a
        /// white key's plate still runs underneath -- as it does on the real
        /// instrument. There, the black key hides it by being solid; here, two
        /// translucent plates simply showed through each other and a bar on a
        /// black key could be read as one on the white key beside it. Depth
        /// writing cannot fix that: transparent geometry draws back-to-front, so
        /// the white plate has already been drawn by the time the black one is.
        /// Opacity is the only lever that actually occludes.
        ///
        /// Only the highlight goes solid. An unlit key stays see-through,
        /// because at rest the point is to look at the real keyboard.
        /// </remarks>
        static readonly Color PreviewFar = new(0.482f, 0.247f, 0.949f, 0.45f);
        static readonly Color PreviewNear = new(0.208f, 0.941f, 1f, 1f);

        /// <summary>Shortest bar drawn, so the furthest note is still visible.</summary>
        const float MinimumPreviewFill = 0.14f;

        void Awake() => BuildMaterials();

        /// <summary>
        /// The lane, for the "play this one next" cue.
        /// </summary>
        /// <remarks>
        /// Assigned rather than serialised so the scene needs no rewiring: the
        /// wizard already holds both halves, and an overlay left without a lane
        /// simply shows no preview instead of throwing.
        /// </remarks>
        public NoteHighway Highway { get; set; }

        /// <summary>
        /// Draw the unlit keys at full strength, for placing them.
        /// </summary>
        /// <remarks>
        /// Fine tuning is the one time every key has to be legible at once: the
        /// whole job is reading the drawn key against the real one under it, and
        /// a marker dimmed to a hint cannot be lined up with anything. Playing is
        /// the opposite — at that strength the board reads as permanently lit,
        /// and "this is the key you play next" has nothing left to say.
        /// </remarks>
        public bool Placing
        {
            get => _placing;
            set
            {
                if (_placing == value) return;
                _placing = value;
                Debug.Log($"[overlay] placing={_placing} "
                        + $"({(_placing ? "full" : "resting")} alpha on unlit keys)");
                RepaintIdle();
            }
        }

        bool _placing;

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

        /// <summary>Draw one key's "coming up" bar.</summary>
        void ShowPreview(NoteHighway.UpcomingNote note, float window)
        {
            if (!_previewFills.TryGetValue(note.Pitch, out var fill) || fill == null) return;

            // 1 at the far edge of the window, 0 at the moment of the strike.
            var away = Mathf.Clamp01(note.Seconds / window);
            var nearness = 1f - away;

            // Grows from the far edge of the key toward the player. The marker
            // is a unit cube scaled to the key, so this is all in its own space
            // and needs no knowledge of how big the key actually is.
            var fraction = Mathf.Lerp(MinimumPreviewFill, 1f, nearness);
            var t = fill.transform;
            t.localScale = new Vector3(0.86f, 1.3f, fraction);
            t.localPosition = new Vector3(0f, 0.25f, -0.5f + fraction * 0.5f);
            fill.gameObject.SetActive(true);

            // Per-key colour without a material each: a property block keeps one
            // shared material and still lets all sixty-one differ.
            _previewBlock ??= new MaterialPropertyBlock();
            _previewBlock.Clear();
            _previewBlock.SetColor(BaseColorId, Color.Lerp(PreviewFar, PreviewNear, nearness));
            fill.SetPropertyBlock(_previewBlock);
        }

        void HidePreview(int pitch)
        {
            if (_previewFills.TryGetValue(pitch, out var fill) && fill != null)
            {
                fill.gameObject.SetActive(false);
            }
        }

        void HideAllPreviews()
        {
            foreach (var fill in _previewFills.Values)
            {
                if (fill != null && fill.gameObject.activeSelf) fill.gameObject.SetActive(false);
            }
        }

        /// <summary>How an unlit key is drawn right now.</summary>
        Material IdleMaterial(int pitch)
        {
            var black = KeyboardLayout.IsBlack(pitch);
            if (_placing) return black ? _idleBlack : _idleWhite;
            return black ? _restingBlack : _restingWhite;
        }

        /// <summary>Redraw every key that is not currently lit.</summary>
        /// <remarks>
        /// Needed because the idle material is only otherwise reassigned to keys
        /// that were lit last frame. Without this, entering fine tuning would
        /// brighten the handful of keys being played and leave the rest faint.
        /// </remarks>
        void RepaintIdle()
        {
            foreach (var pair in _keyMarkers)
            {
                if (pair.Value == null || _litLastFrame.Contains(pair.Key)) continue;
                SetMaterial(pair.Value, IdleMaterial(pair.Key));
            }
        }

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
                    SetMaterial(marker, IdleMaterial(pitch));
                }
            }
            _litLastFrame.Clear();

            // What is coming, drawn inside the keys rather than over them.
            //
            // One flat colour across every upcoming key answers "these are
            // next" but not "in what order", which is the only question worth
            // asking of a run. So each key gets a bar that grows as its note
            // approaches and a colour that warms toward the lit one: the key
            // you play next has the longest bar and the hottest colour, and the
            // ranking is readable at a glance without counting anything.
            HideAllPreviews();

            var upcoming = Highway != null ? Highway.Upcoming : null;
            if (upcoming != null)
            {
                var window = Mathf.Max(0.01f, Highway.PreviewSeconds);
                foreach (var note in upcoming) ShowPreview(note, window);
            }

            foreach (var note in notes)
            {
                if (!_keyMarkers.TryGetValue(note.pitch, out var marker) || marker == null) continue;
                // Held is drawn BELOW full strength, and does not clear the
                // bar.
                //
                // Both used to be wrong together: a held key went to the full
                // lit colour and hid its own preview, on the theory that being
                // played beats being due. That leaves a repeated note with
                // nothing to say. The key is already as bright as it can get
                // and the cue that would have said "again" was suppressed by
                // the very finger that is about to have to lift and strike.
                //
                // So held means "your finger is on this one" -- enough to catch
                // a mis-calibrated overlay, which is what it is for -- and the
                // bar keeps its own meaning of "strike this now", growing over
                // the held key when the note comes round again.
                SetMaterial(marker, _held);
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
                    SetMaterial(marker.transform, IdleMaterial(pitch));
                    _keyMarkers[pitch] = marker.transform;

                    // The "coming up" bar lives inside the key, as a child, so
                    // it inherits the key's size and rotation and can be
                    // described purely as a fraction of it. Made once with the
                    // marker rather than per frame: these are created at the
                    // rate an edge drag moves the calibration.
                    var fill = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    fill.name = $"Preview {pitch}";
                    Destroy(fill.GetComponent<Collider>());
                    fill.transform.SetParent(marker.transform, false);
                    fill.transform.localRotation = Quaternion.identity;
                    if (fill.TryGetComponent<Renderer>(out var fillRenderer))
                    {
                        fillRenderer.sharedMaterial = _next;
                        _previewFills[pitch] = fillRenderer;
                    }
                    fill.SetActive(false);
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
            // The bars are children of the markers, so destroying those took
            // them with it; this just drops the now-dangling references.
            _previewFills.Clear();
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

            // Derived, not authored. Three more material assets would each need
            // wiring into the scene by hand, and would sit at the old colour the
            // first time the ones they shadow were changed.
            _restingWhite = Dimmed(_idleWhite, restingOpacity);
            _restingBlack = Dimmed(_idleBlack, restingOpacity);
            _next = Dimmed(_lit, 0.45f);

            // A held key is confirmation, not instruction, so it sits below
            // the lit colour rather than at it. See the held pass for why.
            _held = Dimmed(_lit, 0.45f);

            // The resolved numbers, not the intended ones. A serialised field
            // that did not take, or an asset edited since, both look identical
            // from inside a headset -- and neither is guessable from outside.
            Debug.Log($"[overlay] alpha idle={Alpha(_idleWhite):F3}/{Alpha(_idleBlack):F3} "
                    + $"resting={Alpha(_restingWhite):F3}/{Alpha(_restingBlack):F3} "
                    + $"(restingOpacity={restingOpacity:F2}) "
                    + $"lit={Alpha(_lit):F3} next={Alpha(_next):F3}");
        }

        /// <summary>The alpha a material actually renders at.</summary>
        static float Alpha(Material material)
        {
            if (material == null) return -1f;
            if (material.HasProperty("_BaseColor")) return material.GetColor("_BaseColor").a;
            if (material.HasProperty("_Color")) return material.GetColor("_Color").a;
            return -1f;
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

        /// <summary>A copy of a material at a fraction of its opacity.</summary>
        static Material Dimmed(Material source, float scale)
        {
            var copy = new Material(source);

            if (copy.HasProperty("_BaseColor"))
            {
                var colour = copy.GetColor("_BaseColor");
                colour.a *= scale;
                copy.SetColor("_BaseColor", colour);
            }

            if (copy.HasProperty("_Color"))
            {
                var colour = copy.GetColor("_Color");
                colour.a *= scale;
                copy.SetColor("_Color", colour);
            }

            return copy;
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
