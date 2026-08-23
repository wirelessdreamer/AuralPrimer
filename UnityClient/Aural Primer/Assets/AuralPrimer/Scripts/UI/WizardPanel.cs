// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The wizard panel: world-anchored, grabbable, XRI-interactable.
//
// It is placed once and then STAYS THERE. An earlier version billboarded and
// followed the head, which makes a panel impossible to look away from and
// impossible to put somewhere useful — in MR the whole point is that a thing
// occupies a place in your room. Summon re-places it only when explicitly asked.
//
// Visual language: "Neon Rhythm" — one hue per meaning, so colour carries
// information rather than mood.
//
//   violet  #7b3ff2  structure: borders, the lane, anything inert
//   magenta #ff2e88  notes on their way, and recording
//   cyan    #35f0ff  NOW: the hit line, a sounding key, the pressed control
//
// Kept close to the desktop client's purple-and-cyan so the two stop reading
// as different products, but pushed hotter: a panel competing with a real room
// seen through passthrough needs more separation than one on a monitor.
//
// Builds its own hierarchy at runtime rather than depending on a prefab, so it
// still works on a fresh clone (the MR template assets are not in version
// control) and the scene cannot lose its UI to a broken reference.

using System;
using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit.Attachment;
using UnityEngine.XR.Interaction.Toolkit.Interactables;
using UnityEngine.XR.Interaction.Toolkit.UI;

namespace AuralPrimer.UI
{
    public sealed class WizardPanel : MonoBehaviour
    {
        [Tooltip("Metres in front of the user when first placed or re-summoned.")]
        [SerializeField] float distanceMetres = 0.85f;

        [Tooltip("Metres above eye level — up, away from the keys, where hand "
               + "tracking is dependable.")]
        [SerializeField] float heightOffsetMetres = 0.1f;

        [Tooltip("Chakra Petch Bold, for the title and button caps.")]
        [SerializeField] TMP_FontAsset displayFont;

        [Tooltip("Chakra Petch SemiBold, for body and status copy.")]
        [SerializeField] TMP_FontAsset bodyFont;

        [Tooltip("Panel width in metres. Height follows the canvas aspect. Sized "
               + "to sit within arm's reach and be touched, rather than to be "
               + "readable from across the room.")]
        [SerializeField] float widthMetres = 0.34f;

        Canvas _canvas;
        TextMeshProUGUI _title;
        TextMeshProUGUI _body;
        TextMeshProUGUI _status;
        BoxCollider _grabBounds;
        BoxCollider _topBounds;
        RectTransform _buttonRow;
        UnityEngine.UI.Slider _scrub;
        RectTransform _scrubRow;
        Action<float> _onScrub;
        bool _suppressScrubCallback;
        readonly List<GameObject> _buttons = new();
        string[] _buttonLabels = System.Array.Empty<string>();
        bool _placed;
        float _waitedSeconds;

        public bool IsVisible => _canvas != null && _canvas.enabled;

        /// <summary>Panel width in metres, for whatever is positioning it.</summary>
        public float WidthMetres => widthMetres;

        void Awake() => Build();

        void Update()
        {
            // Place once the headset actually knows where it is looking.
            //
            // On Quest the camera still reads as identity for the first frames
            // while the XR session comes up, so placing in Start pinned the
            // panel to the rig origin — which is the user's head, and looks
            // exactly like a panel that was never unpinned. Waiting for a real
            // pose costs a few frames and puts it where the user is facing.
            if (_placed) return;

            var camera = Camera.main;
            if (camera == null) return;

            _waitedSeconds += Time.unscaledDeltaTime;

            var pose = camera.transform;
            var posed = pose.position.sqrMagnitude > 1e-6f
                     || Quaternion.Angle(pose.rotation, Quaternion.identity) > 0.5f;

            // Place anyway after a moment: seated dead ahead at the origin is a
            // legitimate pose, and a panel that never appears is worse than one
            // placed from a default.
            if (posed || _waitedSeconds > 2f) PlaceInFrontOfUser();
        }

        /// <summary>
        /// Move the panel in front of the user and face it toward them, once.
        /// This is what the summon gesture does — a panel behind you is the same
        /// as no panel — but it does not run every frame.
        /// </summary>
        public void PlaceInFrontOfUser()
        {
            var camera = Camera.main;
            if (camera == null) return;

            var forward = camera.transform.forward;
            forward.y = 0f;
            if (forward.sqrMagnitude < 1e-4f) forward = Vector3.forward;
            forward.Normalize();

            transform.position = camera.transform.position
                               + forward * distanceMetres
                               + Vector3.up * heightOffsetMetres;
            transform.rotation = Quaternion.LookRotation(forward, Vector3.up);
            _placed = true;
            SetVisible(true);
        }

        /// <summary>
        /// Show the panel where it already is.
        /// </summary>
        /// <remarks>
        /// Distinct from summoning on purpose. Anything that merely wants the
        /// panel on screen — a step change, a status update — must use this, or
        /// the panel teleports to wherever the user happens to be looking and
        /// becomes indistinguishable from one bolted to their face.
        /// </remarks>
        public void Show()
        {
            if (!_placed) PlaceInFrontOfUser();
            else SetVisible(true);
        }

        /// <summary>
        /// Park the panel at a specific pose and keep it there.
        /// </summary>
        /// <remarks>
        /// Used once the keyboard is calibrated: the panel belongs above the
        /// instrument, in the world, where it is always findable. A calibrated
        /// keyboard with nothing over it reads as an app that has stopped
        /// working, even when everything behind it is fine.
        /// </remarks>
        public void PlaceAt(Vector3 position, Quaternion rotation)
        {
            transform.SetPositionAndRotation(position, rotation);
            _placed = true;
            SetVisible(true);
        }

        public void SetVisible(bool visible)
        {
            if (_canvas != null) _canvas.enabled = visible;
            // The colliders go with it: a hidden panel that still catches grabs
            // is an invisible obstacle in the middle of the room.
            if (_grabBounds != null) _grabBounds.enabled = visible;
            if (_topBounds != null) _topBounds.enabled = visible;
        }

        public void Toggle()
        {
            if (IsVisible) SetVisible(false);
            else PlaceInFrontOfUser();
        }

        public void SetTitle(string text)
        {
            if (_title != null) _title.text = text;
        }

        public void SetBody(string text)
        {
            if (_body != null) _body.text = text;
        }

        /// <summary>Connection line, always shown, so "is it even talking to the
        /// desktop?" never requires guessing.</summary>
        public void SetStatus(string text, Color color)
        {
            if (_status == null) return;
            _status.text = text;
            _status.color = color;
        }

        /// <summary>
        /// Replace the row of buttons along the bottom. Passing nothing clears it.
        /// </summary>
        /// <remarks>
        /// Rebuilt rather than shown and hidden: the set differs per step, and a
        /// pool of buttons whose labels and handlers are reassigned is how a
        /// button ends up wired to the previous step's action.
        /// </remarks>
        public void SetButtons(params (string label, Action onPress)[] buttons)
        {
            // Rebuild only when the row actually changes.
            //
            // Fine tuning calls this on every frame of a drag, and Destroy is
            // deferred to end of frame — so the old buttons were still present
            // when the new ones were added, stacking a fresh row on top of the
            // last one sixty times a second. That is the text-on-text.
            var labels = new string[buttons?.Length ?? 0];
            for (var i = 0; i < labels.Length; i++) labels[i] = buttons[i].label;

            if (_buttons.Count == labels.Length && SameLabels(labels))
            {
                // Same row, possibly different closures: swap the handlers and
                // leave the objects alone.
                for (var i = 0; i < _buttons.Count; i++)
                {
                    if (_buttons[i] == null) continue;
                    if (!_buttons[i].TryGetComponent<UnityEngine.UI.Button>(out var existing)) continue;
                    existing.onClick.RemoveAllListeners();
                    var press = buttons[i].onPress;
                    existing.onClick.AddListener(() => press?.Invoke());
                }
                return;
            }

            foreach (var button in _buttons)
            {
                if (button == null) continue;
                // Immediate when not playing, so an editor render shows the row
                // that is really there rather than every row ever set.
                if (Application.isPlaying) Destroy(button); else DestroyImmediate(button);
            }
            _buttons.Clear();
            _buttonLabels = labels;

            if (buttons == null || buttons.Length == 0)
            {
                if (_buttonRow != null) _buttonRow.gameObject.SetActive(false);
                SetBodyBottom(BodyBottomNoButtons);
                return;
            }

            _buttonRow.gameObject.SetActive(true);
            // Give the row its space back from the body, which otherwise runs to
            // the bottom of the panel and straight through the buttons. The
            // scrub bar, when shown, sits above them and takes a further strip.
            var scrubShowing = _scrubRow != null && _scrubRow.gameObject.activeSelf;
            SetBodyBottom((scrubShowing ? ScrubRowTop : ButtonRowTop) + 10f);

            const float rowWidth = 920f;
            var gap = 12f;
            var width = (rowWidth - gap * (buttons.Length - 1)) / buttons.Length;

            for (var i = 0; i < buttons.Length; i++)
            {
                var (label, onPress) = buttons[i];
                var rect = NewChild(_buttonRow, $"Button {label}");
                rect.anchorMin = new Vector2(0f, 0f);
                rect.anchorMax = new Vector2(0f, 1f);
                rect.pivot = new Vector2(0f, 0.5f);
                rect.offsetMin = new Vector2(i * (width + gap), 0f);
                rect.offsetMax = new Vector2(i * (width + gap) + width, 0f);

                var image = rect.gameObject.AddComponent<UnityEngine.UI.Image>();
                image.color = new Color(0.482f, 0.247f, 0.949f, 0.22f); // violet fill
                // The button IS the ray target; its label must not eat the hit.
                image.raycastTarget = true;

                var button = rect.gameObject.AddComponent<UnityEngine.UI.Button>();
                button.targetGraphic = image;
                var colors = button.colors;
                colors.highlightedColor = new Color(0.482f, 0.247f, 0.949f, 0.55f); // violet, hot
                colors.pressedColor = new Color(0.208f, 0.941f, 1f, 1f); // cyan — acting now
                button.colors = colors;

                var press = onPress;
                var pressedLabel = label;
                button.onClick.AddListener(() =>
                {
                    Debug.Log($"[panel] pressed {pressedLabel}");
                    press?.Invoke();
                });

                var text = NewText(rect, "Label", 34, FontStyles.Bold, Vector2.zero, Vector2.zero);
                Stretch((RectTransform)text.transform);
                text.text = label;
                text.alignment = TextAlignmentOptions.Center;
                // The row splits a fixed width, so a five-button step gives each
                // label far less room than a two-button one.
                text.fontStyle = FontStyles.Bold | FontStyles.UpperCase;
                text.characterSpacing = 6f;
                text.enableAutoSizing = true;
                text.fontSizeMin = 18f;
                text.fontSizeMax = 30f;
                text.color = new Color(0.886f, 0.816f, 1f); // violet-tinted text

                _buttons.Add(rect.gameObject);
            }
        }

        // Canvas coordinates, measured from its centre; the canvas is 620 tall,
        // so this runs +310 at the top to -310 at the bottom. Laid out as one
        // top-to-bottom stack with no overlaps, because the previous values put
        // the title across the middle of the body and ran the body underneath
        // the buttons — which every structural check passed and one render of
        // the actual panel showed instantly.
        //
        //   tilt bar   294 .. 260
        //   status     250 .. 205
        //   title      200 .. 130
        //   body       120 .. (depends what is below it)
        //   scrub     -152 .. -192
        //   buttons   -202 .. -252
        //   move bar  -260 .. -294
        const float BodyTop = 120f;
        const float BodyBottomNoButtons = -250f;
        const float ButtonRowTop = -202f;
        /// <summary>Top of the scrub strip, when one is shown.</summary>
        const float ScrubRowTop = -152f;

        /// <summary>Move the body's lower edge, holding its top in place.</summary>
        /// <summary>
        /// Show a scrub bar, or hide it when <paramref name="onScrub"/> is null.
        /// </summary>
        /// <remarks>
        /// A slider rather than skip buttons because finding a moment in a take
        /// is a continuous search — you drag until you see the bit you meant,
        /// and stepping there in fixed jumps turns that into arithmetic.
        /// </remarks>
        public void SetScrub(Action<float> onScrub, float normalised = 0f)
        {
            _onScrub = onScrub;

            if (onScrub == null)
            {
                if (_scrubRow != null) _scrubRow.gameObject.SetActive(false);
                return;
            }

            _scrubRow.gameObject.SetActive(true);

            // Setting the value fires onValueChanged, so a progress update while
            // playing would be indistinguishable from the user dragging — and
            // would seek to where playback already is, every frame.
            _suppressScrubCallback = true;
            _scrub.SetValueWithoutNotify(Mathf.Clamp01(normalised));
            _suppressScrubCallback = false;
        }

        /// <summary>Move the scrub handle without treating it as a seek.</summary>
        public void SetScrubPosition(float normalised)
        {
            if (_scrub == null || _scrubRow == null || !_scrubRow.gameObject.activeSelf) return;
            _scrub.SetValueWithoutNotify(Mathf.Clamp01(normalised));
        }

        bool SameLabels(string[] labels)
        {
            if (_buttonLabels.Length != labels.Length) return false;
            for (var i = 0; i < labels.Length; i++)
            {
                if (_buttonLabels[i] != labels[i]) return false;
            }
            return true;
        }

        void SetBodyBottom(float bottom)
        {
            if (_body == null) return;
            var rect = (RectTransform)_body.transform;
            rect.sizeDelta = new Vector2(920f, BodyTop - bottom);
            rect.anchoredPosition = new Vector2(0f, (BodyTop + bottom) * 0.5f);
        }

        void Build()
        {
            const float canvasWidth = 1000f;
            const float canvasHeight = 620f;
            var scale = widthMetres / canvasWidth;

            var canvasGo = new GameObject("Canvas");
            canvasGo.transform.SetParent(transform, false);
            _canvas = canvasGo.AddComponent<Canvas>();
            _canvas.renderMode = RenderMode.WorldSpace;

            var rect = _canvas.GetComponent<RectTransform>();
            rect.sizeDelta = new Vector2(canvasWidth, canvasHeight);
            rect.localScale = Vector3.one * scale;

            // XRI's raycaster is what lets an interactor ray actually hit this
            // canvas; a plain world-space canvas is inert to XR input without it.
            canvasGo.AddComponent<TrackedDeviceGraphicRaycaster>();

            // Two stacked rects rather than one: UGUI's Image has no stroke, and
            // the violet edge is what separates the panel from a lit room behind
            // it. The outer rect IS the border; the inner one is inset over it.
            var edge = NewChild(canvasGo.transform, "Edge");
            var edgeImage = edge.gameObject.AddComponent<UnityEngine.UI.Image>();
            edgeImage.color = new Color(0.482f, 0.247f, 0.949f, 0.95f); // violet
            // MUST NOT catch the ray. Image.raycastTarget defaults to true, and
            // this one covers the whole panel — so every laser hit landed on the
            // backdrop as a UI hover and the grab colliders behind the drag bars
            // never saw a thing. The bars drew perfectly and could not be moved.
            edgeImage.raycastTarget = false;
            Stretch(edge);

            var background = NewChild(canvasGo.transform, "Background");
            var image = background.gameObject.AddComponent<UnityEngine.UI.Image>();
            image.color = new Color(0.078f, 0.039f, 0.149f, 0.90f); // panel ground
            image.raycastTarget = false; // same reason as the edge above
            Stretch(background);
            background.offsetMin = new Vector2(3f, 3f);
            background.offsetMax = new Vector2(-3f, -3f);

            _title = NewText(canvasGo.transform, "Title", 54, FontStyles.Bold,
                             new Vector2(0f, 165f), new Vector2(920f, 70f));
            _title.color = new Color(0.208f, 0.941f, 1f); // cyan
            _title.alignment = TextAlignmentOptions.Top;
            _title.fontStyle = FontStyles.Bold | FontStyles.UpperCase;
            _title.characterSpacing = 10f;

            _body = NewText(canvasGo.transform, "Body", 38, FontStyles.Normal,
                            new Vector2(0f, -65f), new Vector2(920f, 370f));
            _body.color = new Color(0.886f, 0.816f, 1f, 0.78f); // violet-tinted text
            _body.alignment = TextAlignmentOptions.TopLeft;

            _status = NewText(canvasGo.transform, "Status", 30, FontStyles.Normal,
                              new Vector2(0f, 227f), new Vector2(920f, 46f));
            _status.alignment = TextAlignmentOptions.Center;

            // --- Button row ---------------------------------------------
            // Above the drag handle, so a press near the bottom edge cannot be
            // mistaken for a grab.
            _buttonRow = NewChild(canvasGo.transform, "Buttons");
            _buttonRow.anchorMin = new Vector2(0.5f, 0.5f);
            _buttonRow.anchorMax = new Vector2(0.5f, 0.5f);
            _buttonRow.pivot = new Vector2(0.5f, 0.5f);
            _buttonRow.anchoredPosition = new Vector2(0f, -227f);
            _buttonRow.sizeDelta = new Vector2(920f, 50f);
            _buttonRow.gameObject.SetActive(false);

            // --- Scrub bar ----------------------------------------------
            _scrubRow = NewChild(canvasGo.transform, "Scrub");
            _scrubRow.anchorMin = new Vector2(0.5f, 0.5f);
            _scrubRow.anchorMax = new Vector2(0.5f, 0.5f);
            _scrubRow.pivot = new Vector2(0.5f, 0.5f);
            _scrubRow.anchoredPosition = new Vector2(0f, -172f);
            _scrubRow.sizeDelta = new Vector2(920f, 40f);
            _scrubRow.gameObject.SetActive(false);

            var track = NewChild(_scrubRow, "Track");
            Stretch(track);
            var trackImage = track.gameObject.AddComponent<UnityEngine.UI.Image>();
            trackImage.color = new Color(0.482f, 0.247f, 0.949f, 0.20f); // violet track

            var fill = NewChild(track, "Fill");
            fill.anchorMin = Vector2.zero;
            fill.anchorMax = new Vector2(1f, 1f);
            fill.offsetMin = Vector2.zero;
            fill.offsetMax = Vector2.zero;
            var fillImage = fill.gameObject.AddComponent<UnityEngine.UI.Image>();
            fillImage.color = new Color(0.208f, 0.941f, 1f, 0.95f); // cyan — elapsed
            fillImage.raycastTarget = false;

            var knob = NewChild(_scrubRow, "Knob");
            // Taller than the track so it reads as a grip, but not so tall it
            // runs into the button row 10 units below.
            knob.sizeDelta = new Vector2(36f, 44f);
            var knobImage = knob.gameObject.AddComponent<UnityEngine.UI.Image>();
            knobImage.color = new Color(1f, 0.180f, 0.533f, 1f); // magenta handle
            knobImage.raycastTarget = false;

            _scrub = _scrubRow.gameObject.AddComponent<UnityEngine.UI.Slider>();
            _scrub.targetGraphic = trackImage;
            _scrub.fillRect = fill;
            _scrub.handleRect = knob;
            _scrub.direction = UnityEngine.UI.Slider.Direction.LeftToRight;
            _scrub.minValue = 0f;
            _scrub.maxValue = 1f;
            _scrub.onValueChanged.AddListener(v =>
            {
                if (_suppressScrubCallback) return;
                _onScrub?.Invoke(v);
            });

            // --- Drag handle --------------------------------------------
            // A bar along the bottom edge, the way every windowed thing is
            // moved. The grab target is the handle alone rather than the whole
            // face: a panel that moves wherever you touch it is a panel whose
            // buttons you cannot press.
            var handle = NewChild(canvasGo.transform, "Drag Handle");
            handle.anchorMin = new Vector2(0.5f, 0f);
            handle.anchorMax = new Vector2(0.5f, 0f);
            handle.pivot = new Vector2(0.5f, 0f);
            // 16 units on a 620-unit canvas is 5 mm on the real panel — present,
            // and far too small to find, which is why both bars read as missing.
            handle.anchoredPosition = new Vector2(0f, 16f);
            handle.sizeDelta = new Vector2(440f, 34f);
            var handleImage = handle.gameObject.AddComponent<UnityEngine.UI.Image>();
            handleImage.color = new Color(1f, 0.180f, 0.533f, 0.95f); // magenta grip
            handleImage.raycastTarget = false;

            var moveLabel = NewText(handle, "Move Label", 22, FontStyles.Bold,
                                    Vector2.zero, Vector2.zero);
            Stretch((RectTransform)moveLabel.transform);
            moveLabel.text = "MOVE";
            moveLabel.alignment = TextAlignmentOptions.Center;
            moveLabel.characterSpacing = 8f;
            moveLabel.color = new Color(0.10f, 0.02f, 0.06f);

            // Matches the 34-unit bar plus room either side, so the grab volume is
            // forgiving without swallowing the controls above it.
            var handleHeightMetres = 62f * scale;
            _grabBounds = gameObject.AddComponent<BoxCollider>();
            _grabBounds.size = new Vector3(widthMetres, handleHeightMetres, 0.02f);
            // Sit the collider over the bar at the bottom of the canvas, which is
            // centred on the panel origin.
            _grabBounds.center = new Vector3(0f, -(canvasHeight * scale) * 0.5f + handleHeightMetres * 0.5f, 0f);
            _grabBounds.isTrigger = true;

            // --- Top drag bar -------------------------------------------
            // A second bar along the top edge, feeding the SAME interactable.
            // With a dynamic attach the panel pivots about wherever it was taken
            // hold of, so which bar you grab is the whole control: the bottom one
            // slides it around, the top one has the leverage to raise it and tilt
            // its face, the way you would tip a monitor back.
            var topHandle = NewChild(canvasGo.transform, "Tilt Handle");
            topHandle.anchorMin = new Vector2(0.5f, 1f);
            topHandle.anchorMax = new Vector2(0.5f, 1f);
            topHandle.pivot = new Vector2(0.5f, 1f);
            topHandle.anchoredPosition = new Vector2(0f, -16f);
            topHandle.sizeDelta = new Vector2(440f, 34f);
            var topImage = topHandle.gameObject.AddComponent<UnityEngine.UI.Image>();
            topImage.color = new Color(1f, 0.180f, 0.533f, 0.95f); // magenta grip

            var tiltLabel = NewText(topHandle, "Tilt Label", 22, FontStyles.Bold,
                                    Vector2.zero, Vector2.zero);
            Stretch((RectTransform)tiltLabel.transform);
            tiltLabel.text = "TILT";
            tiltLabel.alignment = TextAlignmentOptions.Center;
            tiltLabel.characterSpacing = 8f;
            tiltLabel.color = new Color(0.10f, 0.02f, 0.06f);
            topImage.raycastTarget = false;

            _topBounds = gameObject.AddComponent<BoxCollider>();
            _topBounds.size = new Vector3(widthMetres, handleHeightMetres, 0.02f);
            _topBounds.center = new Vector3(0f, (canvasHeight * scale) * 0.5f - handleHeightMetres * 0.5f, 0f);
            _topBounds.isTrigger = true;

            // XRGrabInteractable requires a Rigidbody and AddComponent would
            // supply the default one: dynamic, with gravity. The panel would
            // drop to the floor on the first frame. It hangs in the air on
            // purpose, so make the body explicit rather than inheriting one.
            var body = gameObject.AddComponent<Rigidbody>();
            body.isKinematic = true;
            body.useGravity = false;

            var grab = gameObject.AddComponent<XRGrabInteractable>();
            // Force grab: pointing at the panel from across the room and
            // selecting brings it to the hand, rather than leaving it out of
            // reach to be nudged from a distance.
            grab.farAttachMode = InteractableFarAttachMode.Near;
            // Both bars drive this one interactable; dynamic attach is what makes
            // the grab point matter, so grabbing the top tilts rather than slides.
            grab.useDynamicAttach = true;
            // Kinematic and gravity-free: this is a panel being repositioned, not
            // an object being thrown. Instantaneous tracking so it sits where the
            // hand puts it rather than lagging behind.
            grab.movementType = XRBaseInteractable.MovementType.Kinematic;
            grab.useDynamicAttach = true;
            grab.throwOnDetach = false;
            grab.trackRotation = true;

            // Report what was actually built, on the device.
            //
            // A headless check that news up a panel proves the code builds a tilt
            // bar; it cannot prove the running app does, because it never sees
            // this scene, this canvas scale, or a second canvas someone authored
            // years ago. That gap is why "it is in the code" was offered twice as
            // an answer to "it is not on screen".
            var report = $"[panel] built canvas {canvasWidth}x{canvasHeight} scale {scale:F5} "
                       + $"-> {widthMetres:F3}m x {(canvasHeight * scale):F3}m; children:";
            for (var i = 0; i < canvasGo.transform.childCount; i++)
            {
                var child = canvasGo.transform.GetChild(i);
                report += $" {i}:{child.name}";
            }
            Debug.Log(report);
            Debug.Log($"[panel] sibling canvases under this object: "
                    + $"{GetComponentsInChildren<Canvas>(true).Length} (want 1)");

            SetTitle("AuralPrimer");
            SetBody("Starting up…");
            SetStatus("looking for the desktop app…", new Color(0.98f, 0.75f, 0.15f));
        }

        static RectTransform NewChild(Transform parent, string name)
        {
            var go = new GameObject(name, typeof(RectTransform));
            go.transform.SetParent(parent, false);
            return (RectTransform)go.transform;
        }

        static void Stretch(RectTransform rect)
        {
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;
        }

        // Not static: it picks the face from this panel's own assigned fonts.
        TextMeshProUGUI NewText(Transform parent, string name, float size,
                                       FontStyles style, Vector2 anchoredPosition, Vector2 sizeDelta)
        {
            var rect = NewChild(parent, name);
            rect.anchorMin = new Vector2(0.5f, 0.5f);
            rect.anchorMax = new Vector2(0.5f, 0.5f);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.anchoredPosition = anchoredPosition;
            rect.sizeDelta = sizeDelta;

            var text = rect.gameObject.AddComponent<TextMeshProUGUI>();
            // Bold for anything set in caps, SemiBold for reading. Falls through
            // to TMP's default when unassigned rather than rendering nothing.
            var face = style.HasFlag(FontStyles.Bold) ? displayFont : bodyFont;
            if (face != null) text.font = face;
            text.fontSize = size;
            text.fontStyle = style;
            text.textWrappingMode = TextWrappingModes.Normal;
            text.richText = true;
            // Text must not swallow the ray meant for the panel behind it.
            text.raycastTarget = false;
            return text;
        }
    }
}
