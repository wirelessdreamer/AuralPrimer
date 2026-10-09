// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The guitar's neck, found through the controller strapped to its head end.
//
// The printed mount (hardware/quest-guitar-mount) holds a Touch Plus at the
// guitar's head, so the neck is a fixed offset from that controller's grip
// pose. This component reads the grip pose every frame and places its own
// transform on the neck frame -- origin at the nut, X toward the bridge, Y out
// of the fretboard, Z toward the highest string (see FretboardGeometry) -- so
// anything parented to it sits on the real neck and follows the guitar.
//
// Two things about a controller nobody holds, neither verified on a headset
// yet (docs/unity-mr-client-plan-2026-08-18.md, guitar section):
//
//   * Horizon OS switches between controllers and hands, and may drop a
//     controller it sees as set down while the player's hands are tracked.
//     The app asks for simultaneous hands and controllers while the guitar is
//     in use (GuitarAnchor.Meta.cs), and reads the controller through the
//     detached-controller profiles as well as the ordinary one.
//
//   * Its LEDs face the headset only while the player looks toward the neck,
//     so tracking drops whenever they turn away. The last good pose is held
//     briefly and then faded (TrackingHold), rather than snapping or freezing.

using System;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
using UnityEngine.InputSystem.Utilities;
using InputCommonUsages = UnityEngine.InputSystem.CommonUsages;
using NVector3 = System.Numerics.Vector3;
using NQuaternion = System.Numerics.Quaternion;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Guitar Anchor")]
    public sealed partial class GuitarAnchor : MonoBehaviour
    {
        [Tooltip("Optional: the mount's offset file, hardware/quest-guitar-mount/mount_offset.json, "
               + "as a TextAsset. Unset, MountOffset.Cad -- the same numbers, copied -- is the first "
               + "guess. Either way the alignment check replaces it.")]
        [SerializeField] TextAsset mountOffsetJson;

        [Tooltip("How long a lost pose is held before the highway starts to fade. Long enough "
               + "to cover a glance away from the neck.")]
        [SerializeField] float holdSeconds = 0.5f;

        [Tooltip("How long the fade takes once the hold is over.")]
        [SerializeField] float fadeSeconds = 0.75f;

        [Tooltip("From the free controller's aim pose to the point it touches with, along its "
               + "pointing direction, metres. Unmeasured: 0 uses the aim pose's own origin, "
               + "which OpenXR puts at the front of the controller.")]
        [SerializeField] float freeTipOffsetMetres;

        /// <summary>
        /// Controller layouts that can carry the mounted controller, best first.
        /// </summary>
        /// <remarks>
        /// With simultaneous hands and controllers on, a controller nobody holds
        /// is reported as detached, under its own interaction profile
        /// (XR_META_detached_controllers). Which detached profile depends on
        /// which the project enables, so both are listed; the ordinary Touch Plus
        /// is the fallback for when the runtime still treats it as held.
        /// </remarks>
        static readonly string[] MountedLayouts =
        {
            "DetachedQuestTouchPlusController",
            "DetachedOculusTouchController",
            "QuestTouchPlusController",
        };

        /// <summary>Layouts for the free controller in the player's other hand.</summary>
        static readonly string[] HeldLayouts =
        {
            "QuestTouchPlusController",
            "QuestProTouchController",
            "OculusTouchController",
        };

        FretboardProfile _profile;
        MountOffset _offset;
        float _lastTrackedAt = float.NegativeInfinity;
        bool _freeTriggerWasDown;
        Vector3 _gripPosition;
        Quaternion _gripRotation = Quaternion.identity;

        /// <summary>The CAD's offset, or the file's when one is assigned.</summary>
        public MountOffset DefaultOffset { get; private set; } = MountOffset.Cad;

        /// <summary>The offset in force: the profile's measured one, or the default.</summary>
        public MountOffset Offset => _offset;

        /// <summary>Was the mounted controller tracked this frame?</summary>
        public bool IsTracked { get; private set; }

        /// <summary>Has it been tracked at all since the guitar was set up?</summary>
        public bool HasPose { get; private set; }

        /// <summary>1 while tracked, held briefly when lost, then fading to 0.</summary>
        public float Visibility { get; private set; }

        /// <summary>Seconds since the mounted controller was last tracked.</summary>
        public float SecondsLost => HasPose ? Time.unscaledTime - _lastTrackedAt : float.PositiveInfinity;

        /// <summary>
        /// The free controller's tip touched something and its trigger closed,
        /// at this world position. One of the two ways to take a calibration
        /// point; a pinch is the other.
        /// </summary>
        public event Action<Vector3> TipCaptured;

        void Awake()
        {
            if (mountOffsetJson != null)
            {
                if (MountOffset.TryParse(mountOffsetJson.text, out var fromFile)) DefaultOffset = fromFile;
                else Debug.LogWarning("[guitar] mount offset file unreadable; using the built-in CAD offset");
            }
            _offset = DefaultOffset;
        }

        void OnEnable() => SetSimultaneousHandsAndControllers(true);

        void OnDisable()
        {
            SetSimultaneousHandsAndControllers(false);
            IsTracked = false;
        }

        /// <summary>
        /// Ask the runtime to track hands and controllers together, or stop.
        /// </summary>
        /// <remarks>
        /// Only while the guitar is in use: Meta's body tracking cannot run
        /// alongside it, and the recorder's body channel needs that. Implemented
        /// in GuitarAnchor.Meta.cs when the Meta SDK is present.
        /// </remarks>
        partial void SetSimultaneousHandsAndControllers(bool enabled);

        /// <summary>Use this guitar: its hand, and its measured offset if it has one.</summary>
        public void Apply(FretboardProfile profile)
        {
            _profile = profile;
            _offset = profile is { isChecked: true }
                ? new MountOffset(ToNumerics(profile.neckOriginInGrip), ToNumerics(profile.neckRotationInGrip))
                : DefaultOffset;
            HasPose = false;
            _lastTrackedAt = float.NegativeInfinity;
        }

        /// <summary>Try an offset without saving it: the check's result, or a nudge.</summary>
        public void SetOffset(MountOffset offset) => _offset = offset;

        /// <summary>
        /// A world point in the mounted controller's grip frame, as it is now.
        /// </summary>
        /// <remarks>
        /// False while the controller is not tracked. A held pose would do here
        /// as well as anywhere if the guitar were still, but nothing says it is,
        /// and a calibration point taken against a stale pose is wrong for good.
        /// </remarks>
        public bool TryWorldToGrip(Vector3 world, out NVector3 inGrip)
        {
            inGrip = default;
            if (!IsTracked) return false;
            inGrip = FretboardSolver.ToGrip(ToNumerics(_gripPosition), ToNumerics(_gripRotation), ToNumerics(world));
            return true;
        }

        void Update()
        {
            if (_profile == null) return;

            if (TryReadGrip(_profile.controllerOnLeft, out var position, out var rotation))
            {
                _gripPosition = position;
                _gripRotation = rotation;
                _lastTrackedAt = Time.unscaledTime;
                IsTracked = true;
                HasPose = true;
            }
            else
            {
                IsTracked = false;
            }

            Visibility = !HasPose ? 0f
                : IsTracked ? 1f
                : TrackingHold.Visibility(SecondsLost, holdSeconds, fadeSeconds);

            if (HasPose)
            {
                // Held at the last good pose while lost: the highway fades in
                // place rather than following a guess.
                var (neckPosition, neckRotation) =
                    FretboardSolver.NeckPose(ToNumerics(_gripPosition), ToNumerics(_gripRotation), _offset);
                transform.SetPositionAndRotation(ToUnity(neckPosition), ToUnity(neckRotation));
            }

            PollFreeController(!_profile.controllerOnLeft);
        }

        /// <summary>The mounted controller's grip pose, in world space.</summary>
        bool TryReadGrip(bool left, out Vector3 position, out Quaternion rotation)
        {
            position = default;
            rotation = Quaternion.identity;
            var device = FindTracked(left, MountedLayouts);
            if (device == null) return false;
            if (!TryPose(device, "devicePosition", "deviceRotation", out position, out rotation)) return false;
            return true;
        }

        /// <summary>
        /// A trigger press on the controller in the player's other hand
        /// captures where its tip is.
        /// </summary>
        void PollFreeController(bool left)
        {
            var device = FindTracked(left, HeldLayouts);
            var trigger = device?.TryGetChildControl<ButtonControl>("triggerPressed");
            var down = trigger != null && trigger.isPressed;

            if (down && !_freeTriggerWasDown
                && TryPose(device, "pointerPosition", "pointerRotation", out var aim, out var aimRotation))
            {
                TipCaptured?.Invoke(aim + aimRotation * Vector3.forward * freeTipOffsetMetres);
            }
            _freeTriggerWasDown = down;
        }

        /// <summary>
        /// The tracked controller in that hand with one of these layouts, best
        /// layout first; null when none is tracked.
        /// </summary>
        /// <remarks>
        /// Matched by layout name rather than through an input action: one
        /// action bound to several layouts resolves to whichever control is
        /// "most actuated", which for a pose is meaningless, and the hand
        /// interaction profile is an XRController too -- a generic binding
        /// would follow the player's hand once the controller is set down.
        /// </remarks>
        static InputDevice FindTracked(bool left, string[] layouts)
        {
            var usage = left ? InputCommonUsages.LeftHand : InputCommonUsages.RightHand;
            foreach (var layout in layouts)
            {
                foreach (var device in InputSystem.devices)
                {
                    if (!device.added || !device.usages.Contains(usage)) continue;
                    if (device.layout != layout && !InputSystem.IsFirstLayoutBasedOnSecond(device.layout, layout)) continue;
                    var tracked = device.TryGetChildControl<ButtonControl>("isTracked");
                    if (tracked != null && tracked.isPressed) return device;
                }
            }
            return null;
        }

        /// <summary>A pose from a device's controls, converted to world space.</summary>
        static bool TryPose(InputDevice device, string positionName, string rotationName,
                            out Vector3 position, out Quaternion rotation)
        {
            position = default;
            rotation = Quaternion.identity;
            var p = device.TryGetChildControl<Vector3Control>(positionName);
            var r = device.TryGetChildControl<QuaternionControl>(rotationName);
            if (p == null || r == null) return false;

            // Tracked poses are in tracking space, the camera's parent -- the
            // same conversion HandGestures makes, for the same reason: this
            // headset's tracking origin sits over a metre from the world's.
            var space = TrackingSpace;
            position = space != null ? space.TransformPoint(p.ReadValue()) : p.ReadValue();
            rotation = space != null ? space.rotation * r.ReadValue() : r.ReadValue();
            return true;
        }

        static Transform TrackingSpace => Camera.main != null ? Camera.main.transform.parent : null;

        // ---- Unity <-> System.Numerics --------------------------------------
        // The pure maths uses System.Numerics so it can be tested outside Unity.
        // The arithmetic is the same; only the types differ.

        public static NVector3 ToNumerics(Vector3 v) => new(v.x, v.y, v.z);
        public static NQuaternion ToNumerics(Quaternion q) => new(q.x, q.y, q.z, q.w);
        public static Vector3 ToUnity(NVector3 v) => new(v.X, v.Y, v.Z);
        public static Quaternion ToUnity(NQuaternion q) => new(q.X, q.Y, q.Z, q.W);
    }
}
