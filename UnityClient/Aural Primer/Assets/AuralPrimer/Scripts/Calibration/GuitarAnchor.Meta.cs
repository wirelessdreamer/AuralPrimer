// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Simultaneous hands and controllers, which only Meta's Core SDK can switch,
// and the mounted controller read through Meta's OVRInput, which keeps
// tracking a controller nobody holds while the hands are tracked.
//
// Isolated behind META_XR_CORE, like the recorder's body/face/eye channels, so
// the client still compiles without the Meta package -- it then simply relies
// on whatever the runtime does by default, and the setup tells the player what
// to change if the controller on the guitar keeps disappearing.

using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed partial class GuitarAnchor
    {
        /// <summary>
        /// Did the runtime accept simultaneous hands and controllers? Null until asked.
        /// </summary>
        /// <remarks>
        /// False on a runtime without XR_META_simultaneous_hands_and_controllers
        /// or without the Meta SDK; the setup reads this to decide whether to
        /// warn that Horizon OS may swap the set-down controller for hands.
        /// </remarks>
        public bool? SimultaneousModeAccepted { get; private set; }

#if META_XR_CORE
        /// <summary>When to ask again after a refusal (unscaled seconds).</summary>
        float _nextSimultaneousAsk;

        partial void SetSimultaneousHandsAndControllers(bool enabled)
        {
            // The player's hands are on the strings while the controller is
            // strapped to the head: the runtime has to deliver both at once.
            // This is Meta's documented use for the mode, not a workaround.
            var accepted = OVRPlugin.SetSimultaneousHandsAndControllersEnabled(enabled);
            var was = SimultaneousModeAccepted;
            if (enabled) SimultaneousModeAccepted = accepted;
            // Logged on a change only: a refusal is retried every second.
            if (!enabled || was != SimultaneousModeAccepted)
            {
                Debug.Log($"[guitar] simultaneous hands and controllers {(enabled ? "on" : "off")}: "
                        + (accepted ? "accepted" : "refused (retrying)"));
            }
        }

        partial void KeepSimultaneousHandsAndControllers()
        {
            if (SimultaneousModeAccepted == true || Time.unscaledTime < _nextSimultaneousAsk) return;
            _nextSimultaneousAsk = Time.unscaledTime + 1f;
            SetSimultaneousHandsAndControllers(true);
        }

        // OVRInput reports Meta's legacy controller pose, not the OpenXR grip
        // the mount offset is measured from. OVRInput converts grip to legacy
        // with a fixed offset (OVRInput.cs, _gripToVrapiOffset); this is that
        // offset, applied the other way.
        static readonly Vector3 LegacyToGripPosition = new(0f, -0.03f, -0.04f);
        static readonly Quaternion LegacyToGripRotation = new(-0.5f, 0f, 0f, 0.86603f);

        // Meta's tracking space to Unity's (the camera's parent), learned by
        // comparing the headset's pose in each. Both are the same OpenXR
        // session's, so this should settle at identity; it is averaged slowly
        // because the two head poses are sampled at slightly different times.
        bool _hasSpaceFix;
        Vector3 _spaceFixPosition;
        Quaternion _spaceFixRotation = Quaternion.identity;
        float _spaceFixLoggedAt = float.NegativeInfinity;

        partial void ReadMetaGrip(bool left, ref bool found, ref Vector3 position, ref Quaternion rotation)
        {
            if (!OVRPlugin.initialized) return;
            var controller = left ? OVRInput.Controller.LTouch : OVRInput.Controller.RTouch;
            // Simultaneous mode keeps this reporting when the controller is set
            // down (or strapped on) while the hands are tracked: OVRCameraRig
            // reads the same call for its detached anchors.
            if (!OVRInput.GetControllerPositionTracked(controller)
                || !OVRInput.GetControllerOrientationTracked(controller)) return;

            var q = OVRInput.GetLocalControllerRotation(controller);
            var p = OVRInput.GetLocalControllerPosition(controller) + q * LegacyToGripPosition;
            q *= LegacyToGripRotation;

            UpdateSpaceFix();
            p = _spaceFixPosition + _spaceFixRotation * p;
            q = _spaceFixRotation * q;

            var space = TrackingSpace;
            position = space != null ? space.TransformPoint(p) : p;
            rotation = space != null ? space.rotation * q : q;
            found = true;
        }

        void UpdateSpaceFix()
        {
            var camera = Camera.main;
            if (camera == null || !OVRPlugin.GetNodePositionTracked(OVRPlugin.Node.EyeCenter)) return;
            var head = OVRPlugin.GetNodePose(OVRPlugin.Node.EyeCenter, OVRPlugin.Step.Render).ToOVRPose();
            var unity = camera.transform;
            var rotation = unity.localRotation * Quaternion.Inverse(head.orientation);
            var position = unity.localPosition - rotation * head.position;
            if (!_hasSpaceFix)
            {
                _spaceFixPosition = position;
                _spaceFixRotation = rotation;
                _hasSpaceFix = true;
            }
            else
            {
                _spaceFixPosition = Vector3.Lerp(_spaceFixPosition, position, 0.02f);
                _spaceFixRotation = Quaternion.Slerp(_spaceFixRotation, rotation, 0.02f);
            }
            if (Time.unscaledTime - _spaceFixLoggedAt > 10f)
            {
                _spaceFixLoggedAt = Time.unscaledTime;
                Debug.Log($"[guitar] Meta to Unity tracking space: {_spaceFixPosition.magnitude * 100f:0.0} cm, "
                        + $"{Quaternion.Angle(Quaternion.identity, _spaceFixRotation):0.0} deg");
            }
        }
#endif
    }
}
