// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Pinch and palm-up detection straight from XR Hands joint data.
//
// Deliberately not built on XRI interactors: the wizard needs a pinch POSITION
// in world space (to mark a key edge), not a UI click, and the summon gesture
// must work with no interactable in front of it. Reading joints directly is
// both simpler and more precise for that.

using System;
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

        XRHandSubsystem _hands;
        bool _leftPinching, _rightPinching;
        float _palmUpSince = -1f;

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

            if (!facing)
            {
                _palmUpSince = -1f;
                return;
            }

            if (_palmUpSince < 0f)
            {
                _palmUpSince = Time.unscaledTime;
            }
            else if (Time.unscaledTime - _palmUpSince >= palmUpHoldSeconds)
            {
                _palmUpSince = -1f;
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
            return Vector3.Dot(-palmNormal, toCamera) > 0.75f;
        }

        static bool TryGetJointPosition(XRHand hand, XRHandJointID id, out Vector3 position)
        {
            if (hand.GetJoint(id).TryGetPose(out var pose))
            {
                position = pose.position;
                return true;
            }
            position = default;
            return false;
        }
    }
}
