// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The floating panel: connection status and wizard instructions.
//
// Builds its own hierarchy at runtime rather than depending on a prefab. That
// keeps it working on a fresh clone (the MR template assets are not in version
// control) and means the scene cannot lose its UI to a broken reference.
//
// It is visible from the first frame on purpose. A summon gesture is a fine way
// to RECALL a panel; it is a terrible way to discover one exists, and a build
// that shows nothing until you guess the right gesture is indistinguishable
// from a build that is broken.

using TMPro;
using UnityEngine;

namespace AuralPrimer.UI
{
    public sealed class WizardPanel : MonoBehaviour
    {
        [Tooltip("Metres in front of the user when summoned or first shown.")]
        [SerializeField] float distanceMetres = 0.9f;

        [Tooltip("Metres above eye level — deliberately up, away from the keys, "
               + "where hand tracking is dependable.")]
        [SerializeField] float heightOffsetMetres = 0.15f;

        Canvas _canvas;
        TextMeshProUGUI _title;
        TextMeshProUGUI _body;
        TextMeshProUGUI _status;
        Transform _cameraTransform;

        public bool IsVisible => _canvas != null && _canvas.enabled;

        void Awake()
        {
            Build();
            _cameraTransform = Camera.main != null ? Camera.main.transform : null;
        }

        void Start()
        {
            // Place immediately so the first frame already shows something.
            PlaceInFrontOfUser();
        }

        void LateUpdate()
        {
            if (_cameraTransform == null)
            {
                _cameraTransform = Camera.main != null ? Camera.main.transform : null;
                if (_cameraTransform == null) return;
                PlaceInFrontOfUser();
            }

            // Face the user without rolling with their head tilt.
            var toCamera = transform.position - _cameraTransform.position;
            toCamera.y = 0f;
            if (toCamera.sqrMagnitude > 1e-4f)
            {
                transform.rotation = Quaternion.LookRotation(toCamera.normalized, Vector3.up);
            }
        }

        /// <summary>Move the panel back in front of the user. This is what the
        /// palm-up summon does — a panel you cannot find is the same as no
        /// panel, so summon repositions rather than merely toggling.</summary>
        public void PlaceInFrontOfUser()
        {
            if (_cameraTransform == null) return;

            var forward = _cameraTransform.forward;
            forward.y = 0f;
            if (forward.sqrMagnitude < 1e-4f) forward = Vector3.forward;
            forward.Normalize();

            transform.position = _cameraTransform.position
                               + forward * distanceMetres
                               + Vector3.up * heightOffsetMetres;
            SetVisible(true);
        }

        public void SetVisible(bool visible)
        {
            if (_canvas != null) _canvas.enabled = visible;
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
            var canvasGo = new GameObject("Canvas");
            canvasGo.transform.SetParent(transform, false);
            _canvas = canvasGo.AddComponent<Canvas>();
            _canvas.renderMode = RenderMode.WorldSpace;

            var rect = _canvas.GetComponent<RectTransform>();
            rect.sizeDelta = new Vector2(1000f, 620f);
            // 1000 units across mapped to ~0.5 m: comfortable reading size at
            // arm's length without dominating the view.
            rect.localScale = Vector3.one * 0.0005f;

            var background = NewChild(canvasGo.transform, "Background");
            var image = background.gameObject.AddComponent<UnityEngine.UI.Image>();
            image.color = new Color(0.03f, 0.05f, 0.09f, 0.88f);
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
            text.enableWordWrapping = true;
            text.richText = true;
            return text;
        }
    }
}
