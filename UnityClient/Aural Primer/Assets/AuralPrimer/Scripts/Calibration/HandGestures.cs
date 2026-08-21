// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Pinch and palm-up detection straight from XR Hands joint data.
//
// Deliberately not built on XRI interactors: the wizard needs a pinch POSITION
// in world space (to mark a key edge), not a UI click, and the summon gesture
// must work with no interactable in front of it. Reading joints directly is
// both simpler and more precise for that.

using System;
using Unity.XR.CoreUtils;
using UnityEngine;
using UnityEngine.XR.Hands;
using UnityEngine.XR.Management;

namespace AuralPrimer.Calibration
{
    public sealed class HandGestures : MonoBehaviour
    {
        [Tooltip("Thumb-to-index distance below which a pinch is registered.")]
        [SerializeField] float pinchEnterMetres = 0.02f;

        [Tooltip("And above which it is released. Wider than enter, so a hand "
               + "hovering at the threshold does not chatter.")]
        [SerializeField] float pinchExitMetres = 0.035f;

        [Tooltip("Seconds a palm must face the user before the menu is summoned.")]
        [SerializeField] float palmUpHoldSeconds = 0.8f;

        [Tooltip("How long the palm may look away before the hold is abandoned. "
               + "Absorbs tracking blips rather than restarting the gesture.")]
        [SerializeField] float palmUpGraceSeconds = 0.25f;

        [Tooltip("How squarely the palm must face the user, as a dot product. "
               + "1 is exactly on; lower is a wider, more forgiving cone.")]
        [SerializeField] float palmFacingDot = 0.6f;

        XRHandSubsystem _hands;
        XROrigin _origin;
        bool _leftPinching, _rightPinching;
        float _palmUpSince = -1f;
        float _lastFacingAt = float.NegativeInfinity;
        bool _summonLatched;

        /// <summary>Fired once when a pinch closes, with the pinch point in world space.</summary>
        public event Action<Vector3> PinchStarted;

        /// <summary>Fired when a palm has faced the user long enough to mean "show me the menu".</summary>
        public event Action MenuSummoned;

        public bool HandsAvailable => _hands is { running: true };
        public bool IsPinching => _leftPinching || _rightPinching;

        /// <summary>Current pinch point, valid while <see cref="IsPinching"/>.</summary>
        public Vector3 PinchPosition { get; private set; }

        void Update()
        {
            if (_hands == null || !_hands.running)
            {
                TryAcquireSubsystem();
                if (_hands == null) return;
            }

            UpdateHand(_hands.leftHand, ref _leftPinching);
            UpdateHand(_hands.rightHand, ref _rightPinching);
            UpdatePalmUp();
        }

        void TryAcquireSubsystem()
        {
            var loader = XRGeneralSettings.Instance?.Manager?.activeLoader;
            _hands = loader?.GetLoadedSubsystem<XRHandSubsystem>();
            if (_origin == null) _origin = FindFirstObjectByType<XROrigin>();
        }

        void UpdateHand(XRHand hand, ref bool pinching)
        {
            if (!hand.isTracked)
            {
                pinching = false;
                return;
            }

            if (!TryGetJointPosition(hand, XRHandJointID.ThumbTip, out var thumb) ||
                !TryGetJointPosition(hand, XRHandJointID.IndexTip, out var index))
            {
                pinching = false;
                return;
            }

            var distance = Vector3.Distance(thumb, index);
            var midpoint = (thumb + index) * 0.5f;

            // Separate enter/exit thresholds: a single threshold makes a hand
            // resting near it fire repeatedly, which during calibration would
            // scatter several "edge" marks a centimetre apart.
            if (!pinching && distance <= pinchEnterMetres)
            {
                pinching = true;
                PinchPosition = midpoint;
                PinchStarted?.Invoke(midpoint);
            }
            else if (pinching)
            {
                PinchPosition = midpoint;
                if (distance >= pinchExitMetres) pinching = false;
            }
        }

        /// <summary>
        /// Palm facing the user, held. The gesture people expect for "bring up
        /// the menu", and it works with hands raised — which is where tracking
        /// is dependable, unlike fingers resting on keys.
        /// </summary>
        void UpdatePalmUp()
        {
            var facing = IsPalmFacingUser(_hands.leftHand) || IsPalmFacingUser(_hands.rightHand);
            var now = Time.unscaledTime;
            if (facing) _lastFacingAt = now;

            // Tolerate a blink. Requiring every frame of the hold to pass meant
            // one dropped frame — or one wobble across the edge of the cone —
            // silently restarted the whole gesture, so the menu appeared only
            // sometimes and for no reason the user could see.
            if (now - _lastFacingAt > palmUpGraceSeconds)
            {
                _palmUpSince = -1f;
                _summonLatched = false;
                return;
            }

            // Summon once per gesture: the palm must drop before it re-arms,
            // or holding it up would fire again every hold-length.
            if (_summonLatched) return;

            if (_palmUpSince < 0f)
            {
                _palmUpSince = now;
            }
            else if (now - _palmUpSince >= palmUpHoldSeconds)
            {
                _palmUpSince = -1f;
                _summonLatched = true;
                MenuSummoned?.Invoke();
            }
        }

        bool IsPalmFacingUser(XRHand hand)
        {
            if (!hand.isTracked) return false;
            if (!hand.GetJoint(XRHandJointID.Palm).TryGetPose(out var palm)) return false;

            var camera = Camera.main;
            if (camera == null) return false;

            // The palm's own up axis points out of the back of the hand, so a
            // palm turned toward the face points its -up at the camera.
            var toCamera = (camera.transform.position - palm.position).normalized;
            var palmNormal = palm.rotation * Vector3.up;
            return Vector3.Dot(-palmNormal, toCamera) > palmFacingDot;
        }

        /// <summary>
        /// A joint position in world space.
        /// </summary>
        /// <remarks>
        /// Joint poses are relative to the XR Origin, not the world. This
        /// headset reports a Device tracking origin — Floor is unsupported — so
        /// the origin carries a camera offset of over a metre, and the two
        /// spaces are nowhere near each other. Using a raw joint pose as a world
        /// position put every calibration mark somewhere that was not the
        /// keyboard, and the overlay dutifully appeared there.
        /// </remarks>
        bool TryGetJointPosition(XRHand hand, XRHandJointID id, out Vector3 position)
        {
            if (hand.GetJoint(id).TryGetPose(out var pose))
            {
                position = _origin != null
                    ? _origin.Origin.transform.TransformPoint(pose.position)
                    : pose.position;
                return true;
            }
            position = default;
            return false;
        }
    }
}
