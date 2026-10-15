// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Grabbable handles on the guitar's nut and bridge: the keyboard's edge
// handles (EdgeHandles), for a neck.
//
// The nudge buttons ask for "a millimetre toward the body, a degree of roll"
// and a look to see whether it landed. The nut and the bridge are physical
// points on the instrument in front of the player, so the direct thing is to
// take hold of each and put it there. The nut handle is the neck's origin and
// lands exactly where it is let go; the bridge handle aims the neck. Its roll
// is kept either way: two points on the neck's own line cannot say how far it
// is turned about that line.
//
// Both points live in the mounted controller's grip frame, like the offset
// they produce, so the handle not being held stays on the guitar as it moves
// and stays put while the other one is dragged -- the keyboard's rule that
// moving one edge never drags the other. A dragged handle is read against the
// controller's last pose even while it is out of view: the bridge is a long way
// from the head-end mount, and the neck drawn there is the held one too.
//
// The bridge handle stays where it is let go: the neck aims through it, and it
// is not pulled back to one scale length out, which read as the drag being
// ignored. Only a handle flung far from any bridge is refused.

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
        [Tooltip("Handle diameter. Smaller than the keyboard's: a nut is only about 43 mm "
               + "wide, and the handle must not hide what it is being lined up against.")]
        [SerializeField] float sizeMetres = 0.04f;

        /// <summary>
        /// How far the two handles may end up from one scale length apart
        /// before a release is refused, as a share of it.
        /// </summary>
        /// <remarks>
        /// The bridge handle only aims the neck, so its distance is not used --
        /// but a handle a long way from where any bridge could be was flung,
        /// not placed, and saving the neck it aims would lose a good alignment.
        /// </remarks>
        public const float MaxSpanError = 0.25f;

        /// <summary>Raised while a handle is being dragged and the neck has moved.</summary>
        public event Action Moved;

        /// <summary>Raised when a handle is let go: true if the new neck was kept.</summary>
        public event Action<bool> Released;

        GuitarAnchor _anchor;
        float _scaleLength;
        Transform _nut;
        Transform _bridge;
        XRGrabInteractable _nutGrab;
        XRGrabInteractable _bridgeGrab;
        NVector3 _nutInGrip;
        NVector3 _bridgeInGrip;
        /// <summary>Where along the neck the bridge handle sits: where it was let go, else one scale length.</summary>
        float _bridgeDistance;
        MountOffset _offsetAtGrab;

        public bool IsShowing => _nut != null && _bridge != null;

        /// <summary>
        /// The handles' distance apart against the scale length: 0.1 is ten percent.
        /// </summary>
        public float SpanErrorRatio =>
            _scaleLength > 0f
                ? MathF.Abs(NVector3.Distance(_nutInGrip, _bridgeInGrip) - _scaleLength) / _scaleLength
                : float.PositiveInfinity;

        /// <summary>Put a handle on the nut and on the bridge of the neck in force.</summary>
        public void Show(GuitarAnchor anchor, float scaleLengthMetres, Material nutMaterial, Material bridgeMaterial)
        {
            if (anchor == null) { Hide(); return; }

            // Reuse the handles if they are already up, for EdgeHandles' reason:
            // destroying one mid-grab cancels the grab, and the handle springs
            // back the instant it is let go.
            if (IsShowing && _anchor == anchor && Mathf.Approximately(_scaleLength, scaleLengthMetres))
            {
                // The panel re-renders while a handle is held (tracking comes and
                // goes); snapping the held one back onto the neck's line would
                // yank it out of the hand.
                if (!_nutGrab.isSelected && !_bridgeGrab.isSelected) Resync();
                return;
            }

            Hide();
            _anchor = anchor;
            _scaleLength = scaleLengthMetres;
            _bridgeDistance = scaleLengthMetres;
            // Parented under the neck so they go when the guitar does; placed in
            // world space from the grip frame every frame, so the parent's own
            // pose never enters into it.
            _nutGrab = NewHandle("Nut Handle", nutMaterial, anchor.transform);
            _bridgeGrab = NewHandle("Bridge Handle", bridgeMaterial, anchor.transform);
            _nut = _nutGrab.transform;
            _bridge = _bridgeGrab.transform;
            Resync();
        }

        public void Hide()
        {
            if (_nut != null) Destroy(_nut.gameObject);
            if (_bridge != null) Destroy(_bridge.gameObject);
            _nut = null;
            _bridge = null;
            _nutGrab = null;
            _bridgeGrab = null;
            _anchor = null;
        }

        void OnDestroy() => Hide();

        /// <summary>Put the handles back on the nut and bridge of the neck in force.</summary>
        /// <remarks>
        /// After a nudge, an accepted drag (the bridge handle comes back onto the
        /// neck's line, one scale length out) or a refused one.
        /// </remarks>
        public void Resync()
        {
            if (_anchor == null) return;
            var offset = _anchor.Offset;
            _nutInGrip = offset.Position;
            _bridgeInGrip = offset.Position
                          + NVector3.Transform(new NVector3(_bridgeDistance, 0f, 0f), offset.Rotation);
            Place(_nut, _nutInGrip);
            Place(_bridge, _bridgeInGrip);
        }

        void LateUpdate()
        {
            if (!IsShowing) return;

            var nutHeld = _nutGrab.isSelected;
            var bridgeHeld = _bridgeGrab.isSelected;
            var moved = false;

            // Read from the held handle's world position, converted into the grip
            // frame -- never its localPosition, which XRI turns into world
            // coordinates by unparenting it while it is held (EdgeHandles found
            // this the hard way).
            if (nutHeld && _anchor.TryWorldToGripAtLastPose(_nut.position, out var nut) && nut != _nutInGrip)
            {
                _nutInGrip = nut;
                moved = true;
            }
            if (bridgeHeld && _anchor.TryWorldToGripAtLastPose(_bridge.position, out var bridge) && bridge != _bridgeInGrip)
            {
                _bridgeInGrip = bridge;
                moved = true;
            }

            if (moved)
            {
                // Live, while the hand is still moving: the player is lining the
                // drawn frets up with the real ones, and a neck that only shows
                // where it went after they let go cannot be aimed.
                _anchor.SetOffset(FretboardSolver.FromNutAndBridge(_anchor.Offset, _nutInGrip, _bridgeInGrip));
                Moved?.Invoke();
            }

            // The handle nobody holds rides on the guitar.
            if (!nutHeld) Place(_nut, _nutInGrip);
            if (!bridgeHeld) Place(_bridge, _bridgeInGrip);
        }

        void Place(Transform handle, NVector3 inGrip)
        {
            if (handle != null && _anchor != null && _anchor.TryGripToWorld(inGrip, out var world))
            {
                handle.position = world;
            }
        }

        void OnGrabbed()
        {
            // Both handles' grabs start here; only the first sets the undo point.
            if (_nutGrab.isSelected && _bridgeGrab.isSelected) return;
            _offsetAtGrab = _anchor.Offset;
        }

        void OnLetGo(string name)
        {
            // Still holding the other handle: judge the neck when both are down.
            if (_nutGrab.isSelected || _bridgeGrab.isSelected) return;

            var kept = SpanErrorRatio <= MaxSpanError;
            Debug.Log($"[neck-handles] {name} released: nut={_nutInGrip} bridge={_bridgeInGrip} "
                    + $"span={NVector3.Distance(_nutInGrip, _bridgeInGrip) * 1000f:F0} mm "
                    + $"(scale {_scaleLength * 1000f:F0} mm, {SpanErrorRatio:P0} off) -> {(kept ? "kept" : "refused")}");
            if (kept) _bridgeDistance = NVector3.Distance(_nutInGrip, _bridgeInGrip);
            else _anchor.SetOffset(_offsetAtGrab);
            Resync();
            Released?.Invoke(kept);
        }

        XRGrabInteractable NewHandle(string name, Material material, Transform parent)
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
            return grab;
        }
    }
}
