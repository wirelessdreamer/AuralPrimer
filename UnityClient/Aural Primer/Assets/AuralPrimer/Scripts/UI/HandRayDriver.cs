// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Drives an XRI interactor from XR Hands joint data.
//
// The interactors were posed by TrackedPoseDriver reading <HandInteraction>
// bindings. When that interaction profile does not resolve on the device — and
// on Quest it did not, even with the profile enabled — the actions report
// nothing, the driver leaves the transform alone, and the interactor sits at
// its parent's origin. Its parent is the camera offset, so the ray came out of
// the middle of the user's face: painful to look at and useless to point with.
//
// The failure is silent by construction: an unresolved binding is not an error,
// it is simply no data. So rather than depend on a profile resolving, this
// reads the hand joints directly — the same source the calibration pinch uses,
// which is already known to work on this hardware — and drives both the pose
// and the select signal from it.

using Unity.XR.CoreUtils;
using UnityEngine;
using UnityEngine.XR.Hands;
using UnityEngine.XR.Interaction.Toolkit.Inputs.Readers;
using UnityEngine.XR.Interaction.Toolkit.Interactors;
using UnityEngine.XR.Management;

namespace AuralPrimer.UI
{
    [AddComponentMenu("AuralPrimer/Hand Ray Driver")]
    [DefaultExecutionOrder(-100)] // pose before the interactor casts with it
    public sealed class HandRayDriver : MonoBehaviour
    {
        [Tooltip("Which hand drives this interactor.")]
        [SerializeField] bool leftHand;

        [Tooltip("Thumb-to-index distance below which a pinch counts as a press.")]
        [SerializeField] float pinchEnterMetres = 0.02f;

        [Tooltip("And above which it releases. Wider than enter so a hand resting "
               + "at the threshold does not chatter the selection on and off.")]
        [SerializeField] float pinchExitMetres = 0.035f;

        XRHandSubsystem _hands;
        XROrigin _origin;

        /// <summary>Tracking space, as a world transform.</summary>
        /// <remarks>
        /// The camera's own parent, by definition: tracked poses are placed in
        /// whatever space the runtime puts the camera in, so converting a joint
        /// through anything else puts hands and head in different rooms.
        /// Reaching for CameraFloorOffsetObject added an offset the camera was
        /// not itself receiving, which lifted every hand-derived position about
        /// a metre — rays overhead, and a keyboard above the player's eyeline.
        /// </remarks>
        Transform TrackingSpace
        {
            get
            {
                var cam = _origin != null && _origin.Camera != null ? _origin.Camera.transform : null;
                if (cam == null && Camera.main != null) cam = Camera.main.transform;
                return cam != null ? cam.parent : null;
            }
        }
        NearFarInteractor _interactor;
        bool _pinching;
        bool _posed;

        void Awake()
        {
            _interactor = GetComponent<NearFarInteractor>();
            _origin = FindFirstObjectByType<XROrigin>();

            // A pose driver reading bindings that never resolve would otherwise
            // fight this one back to the origin every frame.
            if (TryGetComponent<UnityEngine.InputSystem.XR.TrackedPoseDriver>(out var driver))
                driver.enabled = false;

            if (_interactor != null)
            {
                _interactor.selectInput.inputSourceMode = XRInputButtonReader.InputSourceMode.ManualValue;
                _interactor.uiPressInput.inputSourceMode = XRInputButtonReader.InputSourceMode.ManualValue;
            }

            // Nothing to point with until a hand is actually seen.
            SetVisible(false);
        }

        void Update()
        {
            if (_hands == null || !_hands.running)
            {
                var loader = XRGeneralSettings.Instance?.Manager?.activeLoader;
                _hands = loader?.GetLoadedSubsystem<XRHandSubsystem>();
                if (_hands == null) { Lost(); return; }
            }

            var hand = leftHand ? _hands.leftHand : _hands.rightHand;
            if (!hand.isTracked) { Lost(); return; }

            if (!TryGetJoint(hand, XRHandJointID.IndexProximal, out var knuckle) ||
                !TryGetJoint(hand, XRHandJointID.ThumbTip, out var thumbTip) ||
                !TryGetJoint(hand, XRHandJointID.IndexTip, out var indexTip))
            {
                Lost();
                return;
            }

            Aim(knuckle);
            Press(Vector3.Distance(thumbTip, indexTip));

            if (!_posed) { SetVisible(true); _posed = true; }
        }

        /// <summary>
        /// Point from a virtual shoulder through the hand.
        /// </summary>
        /// <remarks>
        /// Aiming along the finger itself is what people expect and what nobody
        /// can actually hold steady: the ray magnifies every tremor at the far
        /// end. Anchoring the direction behind the shoulder means the hand
        /// positions the ray rather than angling it, which is both steadier and
        /// how the system's own hand rays behave.
        /// </remarks>
        void Aim(Vector3 knuckle)
        {
            var head = Camera.main;
            if (head == null) return;

            var t = head.transform;
            var shoulder = t.position
                         + t.rotation * new Vector3(leftHand ? -0.15f : 0.15f, -0.18f, 0f);

            var direction = knuckle - shoulder;
            if (direction.sqrMagnitude < 1e-6f) return;

            transform.SetPositionAndRotation(
                knuckle,
                Quaternion.LookRotation(direction.normalized, t.up));
        }

        void Press(float pinchDistance)
        {
            var wasPinching = _pinching;
            if (_pinching) _pinching = pinchDistance < pinchExitMetres;
            else _pinching = pinchDistance < pinchEnterMetres;

            if (_interactor == null) return;

            // Both readers get the same signal: one press should grab a handle
            // and click a button, not one or the other depending on the target.
            _interactor.selectInput.QueueManualState(
                _pinching, _pinching ? 1f : 0f, _pinching && !wasPinching, !_pinching && wasPinching);
            _interactor.uiPressInput.QueueManualState(
                _pinching, _pinching ? 1f : 0f, _pinching && !wasPinching, !_pinching && wasPinching);
        }

        /// <summary>Hand gone: release anything held and stop drawing a ray.</summary>
        void Lost()
        {
            if (_pinching)
            {
                _pinching = false;
                if (_interactor != null)
                {
                    _interactor.selectInput.QueueManualState(false, 0f, false, true);
                    _interactor.uiPressInput.QueueManualState(false, 0f, false, true);
                }
            }

            if (_posed) { SetVisible(false); _posed = false; }
        }

        void SetVisible(bool visible)
        {
            if (_interactor != null) _interactor.enabled = visible;
            if (TryGetComponent<LineRenderer>(out var line)) line.enabled = visible;
        }

        /// <summary>
        /// A joint position in world space.
        /// </summary>
        /// <remarks>
        /// Hand joint poses are relative to the XR Origin, while Camera.main and
        /// this transform are in world space. They coincide only while the origin
        /// sits at identity, so mixing them silently works until the moment the
        /// player is recentered or moved.
        /// </remarks>
        bool TryGetJoint(XRHand hand, XRHandJointID id, out Vector3 position)
        {
            if (hand.GetJoint(id).TryGetPose(out var pose))
            {
                position = TrackingSpace != null
                    ? TrackingSpace.TransformPoint(pose.position)
                    : pose.position;
                return true;
            }
            position = default;
            return false;
        }
    }
}
