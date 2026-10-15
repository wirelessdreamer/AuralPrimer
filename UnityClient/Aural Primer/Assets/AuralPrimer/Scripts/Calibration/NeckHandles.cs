// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Grabbable handles for the guitar's nut and bridge: the keyboard's edge
// handles (EdgeHandles), for a neck.
//
// The nudge buttons ask for "a millimetre toward the body, a degree of roll"
// and a look to see whether it landed. The nut and the bridge are physical
// points on the instrument in front of the player, so the direct thing is to
// take hold of each and put it there. The nut handle sets the neck's origin;
// the bridge handle aims the neck and, by its distance from the nut, sets the
// scale length -- snapped to a standard scale when it is close to one, so one
// neck lines up a 25.5" Fender, a 24.75" Gibson or a 24" Mustang. The roll is
// kept either way: two points on the neck's own line cannot say how far it is
// turned about that line.
//
// Each ball floats 7.5 cm out from the back of the neck, opposite the
// fretboard face, over the point it sets (out of the face was the far side
// from the player, and toward the head put them above the neck, into the
// body's way), with a thin line to a small dot on that point. Grabbed with the
// hand ray, a ball rides at a fixed distance along a ray from the shoulder,
// so a point down on the player's lap -- the bridge, especially -- could not
// be reached at all: the ball went up but would not come down. Up here it is
// in easy reach, and moving it moves the point under it by the same amount.
//
// Both points live in the mounted controller's grip frame, like the offset
// they produce, so the handle not being held stays on the guitar as it moves
// and stays put while the other one is dragged -- the keyboard's rule that
// moving one edge never drags the other. A dragged handle is read against the
// controller's last pose even while it is out of view: the bridge is a long
// way from the head-end mount, and the neck drawn there is the held one too.

using System;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit.Attachment;
using UnityEngine.XR.Interaction.Toolkit.Interactables;
using NVector3 = System.Numerics.Vector3;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Neck Handles")]
    public sealed class NeckHandles : MonoBehaviour
    {
        [Tooltip("Handle diameter. Smaller than the keyboard's: it should not hide the "
               + "neck it is being lined up against.")]
        [SerializeField] float sizeMetres = 0.04f;

        [Tooltip("How far out from the back of the neck each ball floats over its point.")]
        [SerializeField] float hoverMetres = 0.075f;

        /// <summary>Raised while a handle is being dragged and the neck has moved.</summary>
        public event Action Moved;

        /// <summary>Raised when a handle is let go: true if the new neck was kept.</summary>
        public event Action<bool> Released;

        GuitarAnchor _anchor;
        Transform _nut;
        Transform _bridge;
        XRGrabInteractable _nutGrab;
        XRGrabInteractable _bridgeGrab;
        Transform _nutLeader;
        Transform _bridgeLeader;
        Transform _nutDot;
        Transform _bridgeDot;
        NVector3 _nutInGrip;
        NVector3 _bridgeInGrip;
        /// <summary>From a point up to its ball, in the grip frame: fixed for a drag, so the drag cannot move its own lift.</summary>
        NVector3 _liftInGrip;
        MountOffset _offsetAtGrab;
        float _scaleAtGrab;

        public bool IsShowing => _nut != null && _bridge != null;

        /// <summary>The scale length the handles set: the nut-to-bridge span, snapped to a standard one.</summary>
        public float ScaleLength { get; private set; }

        /// <summary>Put a handle above the nut and above the bridge of the neck in force.</summary>
        public void Show(GuitarAnchor anchor, float scaleLengthMetres, Material nutMaterial, Material bridgeMaterial)
        {
            if (anchor == null) { Hide(); return; }

            // Reuse the handles if they are already up, for EdgeHandles' reason:
            // destroying one mid-grab cancels the grab, and the handle springs
            // back the instant it is let go.
            if (IsShowing && _anchor == anchor)
            {
                // The panel re-renders while a handle is held (tracking comes and
                // goes, the scale readout changes); snapping the held one back
                // onto the neck's line would yank it out of the hand.
                if (!_nutGrab.isSelected && !_bridgeGrab.isSelected)
                {
                    ScaleLength = scaleLengthMetres;
                    Resync();
                }
                return;
            }

            Hide();
            _anchor = anchor;
            ScaleLength = scaleLengthMetres;
            // Parented under the neck so they go when the guitar does; placed in
            // world space from the grip frame every frame, so the parent's own
            // pose never enters into it.
            (_nutGrab, _nutLeader, _nutDot) = NewHandle("Nut Handle", nutMaterial, anchor.transform);
            (_bridgeGrab, _bridgeLeader, _bridgeDot) = NewHandle("Bridge Handle", bridgeMaterial, anchor.transform);
            _nut = _nutGrab.transform;
            _bridge = _bridgeGrab.transform;
            Resync();
        }

        public void Hide()
        {
            foreach (var t in new[] { _nut, _bridge, _nutLeader, _bridgeLeader, _nutDot, _bridgeDot })
            {
                if (t != null) Destroy(t.gameObject);
            }
            _nut = _bridge = _nutLeader = _bridgeLeader = _nutDot = _bridgeDot = null;
            _nutGrab = null;
            _bridgeGrab = null;
            _anchor = null;
        }

        void OnDestroy() => Hide();

        /// <summary>Put the points back on the nut and bridge of the neck in force.</summary>
        /// <remarks>After a nudge, an accepted drag or a refused one.</remarks>
        public void Resync()
        {
            if (_anchor == null) return;
            var offset = _anchor.Offset;
            _nutInGrip = offset.Position;
            _bridgeInGrip = offset.Position
                          + NVector3.Transform(new NVector3(ScaleLength, 0f, 0f), offset.Rotation);
            _liftInGrip = LiftFor(offset);
        }

        /// <summary>
        /// 7.5 cm out from the back of the neck (the neck's -Y), in the grip frame.
        /// </summary>
        NVector3 LiftFor(MountOffset offset) =>
            NVector3.Transform(new NVector3(0f, -hoverMetres, 0f), offset.Rotation);

        void LateUpdate()
        {
            if (!IsShowing) return;

            var nutHeld = _nutGrab.isSelected;
            var bridgeHeld = _bridgeGrab.isSelected;
            var moved = false;

            // Read from the held ball's world position, converted into the grip
            // frame and dropped by its lift onto the neck -- never its
            // localPosition, which XRI turns into world coordinates by
            // unparenting it while it is held (EdgeHandles found this the hard way).
            if (nutHeld && _anchor.TryWorldToGripAtLastPose(_nut.position, out var nutBall)
                && nutBall - _liftInGrip != _nutInGrip)
            {
                _nutInGrip = nutBall - _liftInGrip;
                moved = true;
            }
            if (bridgeHeld && _anchor.TryWorldToGripAtLastPose(_bridge.position, out var bridgeBall)
                && bridgeBall - _liftInGrip != _bridgeInGrip)
            {
                _bridgeInGrip = bridgeBall - _liftInGrip;
                moved = true;
            }

            if (moved)
            {
                // Live, while the hand is still moving: the player is lining the
                // drawn frets up with the real ones, and a neck that only shows
                // where it went after they let go cannot be aimed.
                _anchor.SetOffset(FretboardSolver.FromNutAndBridge(_anchor.Offset, _nutInGrip, _bridgeInGrip));
                if (FretboardSolver.ScaleFromSpan(NVector3.Distance(_nutInGrip, _bridgeInGrip), out var scale))
                {
                    ScaleLength = scale;
                }
                Moved?.Invoke();
            }

            // The ball nobody holds rides on the guitar, above its point.
            if (!nutHeld) Place(_nut, _nutInGrip + _liftInGrip);
            if (!bridgeHeld) Place(_bridge, _bridgeInGrip + _liftInGrip);
            Lead(_nutLeader, _nutDot, _nut.position, _nutInGrip);
            Lead(_bridgeLeader, _bridgeDot, _bridge.position, _bridgeInGrip);
        }

        void Place(Transform handle, NVector3 inGrip)
        {
            if (handle != null && _anchor != null && _anchor.TryGripToWorld(inGrip, out var world))
            {
                handle.position = world;
            }
        }

        /// <summary>The thin line from a ball down to the dot on the point it sets.</summary>
        void Lead(Transform leader, Transform dot, Vector3 ball, NVector3 pointInGrip)
        {
            if (leader == null || dot == null || !_anchor.TryGripToWorld(pointInGrip, out var point)) return;
            dot.position = point;
            var span = ball - point;
            var length = span.magnitude;
            leader.gameObject.SetActive(length > 1e-4f);
            if (length <= 1e-4f) return;
            // A unit cylinder is 2 long along its Y.
            leader.SetPositionAndRotation((ball + point) * 0.5f, Quaternion.FromToRotation(Vector3.up, span / length));
            leader.localScale = new Vector3(0.003f, length * 0.5f, 0.003f);
        }

        void OnGrabbed()
        {
            // Both handles' grabs start here; only the first sets the undo point.
            if (_nutGrab.isSelected && _bridgeGrab.isSelected) return;
            _offsetAtGrab = _anchor.Offset;
            _scaleAtGrab = ScaleLength;
            _liftInGrip = LiftFor(_anchor.Offset);
        }

        void OnLetGo(string name)
        {
            // Still holding the other handle: judge the neck when both are down.
            if (_nutGrab.isSelected || _bridgeGrab.isSelected) return;

            var span = NVector3.Distance(_nutInGrip, _bridgeInGrip);
            var kept = FretboardSolver.ScaleFromSpan(span, out var scale);
            Debug.Log($"[neck-handles] {name} released: nut={_nutInGrip} bridge={_bridgeInGrip} "
                    + $"span={span * 1000f:F0} mm -> {(kept ? $"kept, scale {scale * 1000f:F1} mm" : "refused")}");
            if (kept) ScaleLength = scale;
            else
            {
                _anchor.SetOffset(_offsetAtGrab);
                ScaleLength = _scaleAtGrab;
            }
            Resync();
            Released?.Invoke(kept);
        }

        (XRGrabInteractable grab, Transform leader, Transform dot) NewHandle(string name, Material material, Transform parent)
        {
            var handle = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            handle.name = name;
            handle.transform.SetParent(parent, false);
            handle.transform.localScale = Vector3.one * sizeMetres;

            if (material != null && handle.TryGetComponent<Renderer>(out var renderer))
            {
                renderer.sharedMaterial = material;
            }

            // Kinematic, no gravity: the default Rigidbody would drop the handle
            // off the guitar on the first frame.
            var body = handle.AddComponent<Rigidbody>();
            body.isKinematic = true;
            body.useGravity = false;

            // Configured as the keyboard's edge handles are, for the reasons
            // EdgeHandles gives: grabbed where it is (no snap to the hand, which
            // would destroy the very point being placed), moved directly rather
            // than through physics (which puts it back on release), no spin and
            // no throw.
            var grab = handle.AddComponent<XRGrabInteractable>();
            grab.farAttachMode = InteractableFarAttachMode.Far;
            grab.movementType = XRBaseInteractable.MovementType.Instantaneous;
            grab.trackRotation = false;
            grab.throwOnDetach = false;
            grab.useDynamicAttach = true;
            grab.selectEntered.AddListener(_ => OnGrabbed());
            grab.selectExited.AddListener(_ => OnLetGo(name));

            // The line down to the neck and the dot on the point: shown, never
            // hit -- a collider on either would catch the ray meant for the ball.
            var leader = Decoration(PrimitiveType.Cylinder, $"{name} Line", material, parent);
            var dot = Decoration(PrimitiveType.Sphere, $"{name} Point", material, parent);
            dot.localScale = Vector3.one * 0.008f;
            return (grab, leader, dot);
        }

        static Transform Decoration(PrimitiveType shape, string name, Material material, Transform parent)
        {
            var go = GameObject.CreatePrimitive(shape);
            go.name = name;
            if (go.TryGetComponent<Collider>(out var collider)) Destroy(collider);
            if (material != null && go.TryGetComponent<Renderer>(out var renderer)) renderer.sharedMaterial = material;
            go.transform.SetParent(parent, false);
            return go.transform;
        }
    }
}
