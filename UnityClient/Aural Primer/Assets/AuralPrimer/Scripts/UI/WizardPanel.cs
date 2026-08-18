// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The wizard panel: world-anchored, grabbable, XRI-interactable.
//
// It is placed once and then STAYS THERE. An earlier version billboarded and
// followed the head, which makes a panel impossible to look away from and
// impossible to put somewhere useful — in MR the whole point is that a thing
// occupies a place in your room. Summon re-places it only when explicitly asked.
//
// Builds its own hierarchy at runtime rather than depending on a prefab, so it
// still works on a fresh clone (the MR template assets are not in version
// control) and the scene cannot lose its UI to a broken reference.

using TMPro;
using UnityEngine;
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

        [Tooltip("Panel width in metres. Height follows the canvas aspect.")]
        [SerializeField] float widthMetres = 0.5f;

        Canvas _canvas;
        TextMeshProUGUI _title;
        TextMeshProUGUI _body;
        TextMeshProUGUI _status;
        BoxCollider _grabBounds;
        bool _placed;

        public bool IsVisible => _canvas != null && _canvas.enabled;

        void Awake() => Build();

        void Start()
        {
            // Place on the first frame so there is never a "nothing appeared"
            // state, then leave it alone.
            if (!_placed) PlaceInFrontOfUser();
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

        public void SetVisible(bool visible)
        {
            if (_canvas != null) _canvas.enabled = visible;
            // The collider goes with it: a hidden panel that still catches grabs
            // is an invisible obstacle in the middle of the room.
            if (_grabBounds != null) _grabBounds.enabled = visible;
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

            var background = NewChild(canvasGo.transform, "Background");
            var image = background.gameObject.AddComponent<UnityEngine.UI.Image>();
            image.color = new Color(0.03f, 0.05f, 0.09f, 0.92f);
            Stretch(background);

            _title = NewText(canvasGo.transform, "Title", 54, FontStyles.Bold,
                             new Vector2(0f, -40f), new Vector2(920f, 90f));
            _title.color = new Color(0.85f, 0.93f, 1f);
            _title.alignment = TextAlignmentOptions.Top;

            _body = NewText(canvasGo.transform, "Body", 38, FontStyles.Normal,
                            new Vector2(0f, -190f), new Vector2(920f, 320f));
            _body.color = new Color(0.80f, 0.86f, 0.95f);
            _body.alignment = TextAlignmentOptions.TopLeft;

            _status = NewText(canvasGo.transform, "Status", 32, FontStyles.Normal,
                              new Vector2(0f, 250f), new Vector2(920f, 60f));
            _status.alignment = TextAlignmentOptions.Bottom;

            // --- Grab ---------------------------------------------------
            // Bounds match the visible panel so the grab target is exactly what
            // the user sees, with a little depth to make it catchable.
            _grabBounds = gameObject.AddComponent<BoxCollider>();
            _grabBounds.size = new Vector3(widthMetres, canvasHeight * scale, 0.02f);
            _grabBounds.isTrigger = true;

            // XRGrabInteractable requires a Rigidbody and AddComponent would
            // supply the default one: dynamic, with gravity. The panel would
            // drop to the floor on the first frame. It hangs in the air on
            // purpose, so make the body explicit rather than inheriting one.
            var body = gameObject.AddComponent<Rigidbody>();
            body.isKinematic = true;
            body.useGravity = false;

            var grab = gameObject.AddComponent<XRGrabInteractable>();
            // Kinematic and gravity-free: this is a panel being repositioned, not
            // an object being thrown. Instantaneous tracking so it sits where the
            // hand puts it rather than lagging behind.
            grab.movementType = XRBaseInteractable.MovementType.Kinematic;
            grab.useDynamicAttach = true;
            grab.throwOnDetach = false;
            grab.trackRotation = true;

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

        static TextMeshProUGUI NewText(Transform parent, string name, float size,
                                       FontStyles style, Vector2 anchoredPosition, Vector2 sizeDelta)
        {
            var rect = NewChild(parent, name);
            rect.anchorMin = new Vector2(0.5f, 0.5f);
            rect.anchorMax = new Vector2(0.5f, 0.5f);
            rect.pivot = new Vector2(0.5f, 0.5f);
            rect.anchoredPosition = anchoredPosition;
            rect.sizeDelta = sizeDelta;

            var text = rect.gameObject.AddComponent<TextMeshProUGUI>();
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
