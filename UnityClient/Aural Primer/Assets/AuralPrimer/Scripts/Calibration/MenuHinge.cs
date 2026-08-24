// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Hangs the menu off the corner of the instrument like a door.
//
// The panel's inner vertical edge is pinned to the keyboard's end corner, and a
// grab anchor on its outer edge swings it about that pin — the same gesture as
// the note display's top bar, applied to the axis that actually matters here.
//
// Pinning beats placing. A free-floating panel has three positions and three
// rotations to get wrong, and every one of them has been got wrong at least
// once in this client: aimed at the head, squared to the room, squared to the
// instrument, too far to touch. Hinged, there is exactly one number — how far
// open it is — and it cannot drift away from the keyboard it belongs to.

using System;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit.Attachment;
using UnityEngine.XR.Interaction.Toolkit.Interactables;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Menu Hinge")]
    public sealed class MenuHinge : MonoBehaviour
    {
        [Tooltip("Diameter of the swing anchor on the menu's outer edge.")]
        [SerializeField] float anchorMetres = 0.045f;

        [Tooltip("How far the menu hangs above the key bed, in metres.")]
        [SerializeField] float liftMetres = 0.3048f;

        /// <summary>Raised while the menu is being swung.</summary>
        public event Action Moved;

        /// <summary>Raised when the anchor is let go.</summary>
        public event Action Released;

        CalibrationProfile _profile;
        Transform _space;
        Transform _anchor;
        float _panelWidth;

        public bool IsShowing => _anchor != null;

        /// <summary>Put the swing anchor on the menu's outer edge.</summary>
        public void Show(CalibrationProfile profile, Transform space, Material material, float panelWidth)
        {
            _panelWidth = panelWidth;

            if (profile == null || !profile.IsCalibrated) { Hide(); return; }
            if (IsShowing && _profile == profile) { Place(); return; }

            Hide();
            _profile = profile;
            _space = space != null ? space : transform;

            var anchor = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            anchor.name = "Menu Swing Anchor";
            anchor.transform.SetParent(_space, false);
            anchor.transform.localScale = Vector3.one * anchorMetres;

            if (material != null && anchor.TryGetComponent<Renderer>(out var renderer))
            {
                renderer.sharedMaterial = material;
            }

            var body = anchor.AddComponent<Rigidbody>();
            body.isKinematic = true;
            body.useGravity = false;

            var grab = anchor.AddComponent<XRGrabInteractable>();
            grab.farAttachMode = InteractableFarAttachMode.Far;
            grab.movementType = XRBaseInteractable.MovementType.Instantaneous;
            grab.trackRotation = false;
            grab.throwOnDetach = false;
            grab.useDynamicAttach = true;
            grab.selectExited.AddListener(_ => Released?.Invoke());

            _anchor = anchor.transform;
            Place();
        }

        public void Hide()
        {
            if (_anchor != null) Destroy(_anchor.gameObject);
            _anchor = null;
            _profile = null;
        }

        void OnDestroy() => Hide();

        void Update()
        {
            if (_profile == null || _anchor == null) return;

            // World, converted into the calibration's space — XRI unparents an
            // interactable while it is held, so localPosition would be lying.
            var inSpace = _space.InverseTransformPoint(_anchor.position);
            var offset = inSpace - HingePoint();

            // Only the swing matters. Height and distance are fixed by the pin,
            // so the anchor's position collapses to one angle about the vertical.
            var up = _profile.CantedUp;
            var flat = Vector3.ProjectOnPlane(offset, up);
            if (flat.sqrMagnitude < 1e-5f) return;

            var yaw = Vector3.SignedAngle(BaseOutward(), flat.normalized, up);
            yaw = Mathf.Clamp(yaw, -150f, 150f);

            if (Mathf.Abs(yaw - _profile.menuYawDegrees) < 1e-3f) return;

            _profile.menuYawDegrees = yaw;
            Moved?.Invoke();
        }

        /// <summary>Put the anchor back on the menu's outer edge.</summary>
        public void Place()
        {
            if (_profile == null || _anchor == null) return;
            _anchor.position = _space.TransformPoint(HingePoint() + Outward() * _panelWidth);
        }

        /// <summary>The corner of the instrument the menu is pinned to.</summary>
        public Vector3 HingePoint()
        {
            var end = _profile.menuOnHighEnd ? _profile.rightEdge : _profile.leftEdge;

            // Lifted clear of the key bed on its own number, not on the lane
            // gap. laneLiftMetres is the play-line's hover over the keys and the
            // note highway reads the same field, so folding the menu's height
            // into it would carry the falling notes up with it.
            return end + _profile.CantedUp * (_profile.laneLiftMetres + liftMetres);
        }

        /// <summary>Straight out along the key bed, away from the instrument.</summary>
        public Vector3 BaseOutward() =>
            _profile.menuOnHighEnd ? _profile.RightAxis : -_profile.RightAxis;

        /// <summary>The direction the menu currently lies in, from its hinge.</summary>
        public Vector3 Outward() =>
            Quaternion.AngleAxis(_profile.menuYawDegrees, _profile.CantedUp) * BaseOutward();
    }
}
