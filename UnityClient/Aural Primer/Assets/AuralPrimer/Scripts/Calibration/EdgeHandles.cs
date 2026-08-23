// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Grabbable handles on the two calibrated edges.
//
// Stepper buttons — narrower, wider, left, right — ask the player to translate
// "that key sits a bit left of the one I'm pressing" into a count of presses on
// an abstract control, then look up to see whether the guess landed. The edges
// are physical points on a real instrument in front of them; the direct thing
// is to take hold of one and put it where it belongs.
//
// XRI already turns a pinch into a select, so an XRGrabInteractable IS the
// control: no new input path, near-grab and ray-grab both work, and the same
// gesture that marked the edges during calibration adjusts them afterwards.

using System;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit.Attachment;
using UnityEngine.XR.Interaction.Toolkit.Interactables;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Edge Handles")]
    public sealed class EdgeHandles : MonoBehaviour
    {
        [Tooltip("Handle diameter. Big enough to grab reliably with hand "
               + "tracking, small enough to see the key underneath it.")]
        [SerializeField] float sizeMetres = 0.055f;

        /// <summary>Raised while a handle is being dragged.</summary>
        public event Action Moved;

        /// <summary>Raised when a handle is let go.</summary>
        public event Action Released;

        CalibrationProfile _profile;
        Transform _space;
        Transform _left;
        Transform _right;

        public bool IsShowing => _left != null && _right != null;

        /// <summary>Put a grabbable handle on each calibrated edge.</summary>
        public void Show(CalibrationProfile profile, Transform anchorSpace,
                         Material leftMaterial, Material rightMaterial)
        {
            if (profile == null || !profile.IsCalibrated) { Hide(); return; }

            // Reuse the handles if they are already up. Rebuilding destroys the
            // interactables, and destroying one mid-grab cancels the grab and
            // recreates it at the stored edge — which looks exactly like a
            // handle springing back the instant you let go.
            if (IsShowing && _profile == profile)
            {
                Debug.Log("[handles] already showing; left in place");
                return;
            }

            Hide();
            _profile = profile;

            // Parented into the same transform the keys are drawn under, so a
            // handle's localPosition IS the stored edge value and a drag needs no
            // conversion in either direction. Parenting to a different-but-related
            // transform is the subtle version of this bug: everything looks right
            // until the two frames differ by an offset nobody set deliberately.
            var parent = anchorSpace != null ? anchorSpace : transform;
            // Remembered explicitly. The profile's numbers mean "in this
            // transform's space", and that has to stay true even when the
            // handle is no longer a child of it — see Update.
            _space = parent;
            _left = NewHandle("Left Edge Handle", profile.leftEdge, leftMaterial, parent);
            _right = NewHandle("Right Edge Handle", profile.rightEdge, rightMaterial, parent);

            // Everything about a misplaced overlay comes down to which transform
            // these numbers are relative to, so say it out loud once.
            Debug.Log($"[handles] parent={(parent != null ? parent.name : "<none>")} "
                    + $"parentLocalPos={(parent != null ? parent.localPosition.ToString("F3") : "-")} "
                    + $"parentWorldPos={(parent != null ? parent.position.ToString("F3") : "-")} "
                    + $"| leftEdge={profile.leftEdge:F3} -> world {_left.position:F3} "
                    + $"| rightEdge={profile.rightEdge:F3} -> world {_right.position:F3}");
        }

        public void Hide()
        {
            if (_left != null) Destroy(_left.gameObject);
            if (_right != null) Destroy(_right.gameObject);
            _left = null;
            _right = null;
            _profile = null;
        }

        void OnDestroy() => Hide();

        void Update()
        {
            if (_profile == null || _left == null || _right == null) return;

            // World position converted into the calibration's space — NEVER
            // localPosition.
            //
            // XRI unparents an interactable while it is held, so localPosition
            // silently becomes world coordinates mid-grab. Those were written
            // straight into anchor-space fields: the keyboard flew apart while
            // dragging, and the release measured 55 degrees off level and was
            // rejected. Converting from world is parent-independent and holds
            // whatever XRI does with the hierarchy.
            var left = _space != null ? _space.InverseTransformPoint(_left.position) : _left.position;
            var right = _space != null ? _space.InverseTransformPoint(_right.position) : _right.position;
            if (left == _profile.leftEdge && right == _profile.rightEdge) return;

            // Written back while the hand is still moving, not on release: the
            // player is lining the overlay up against real keys, and a target
            // that only shows where it landed after they let go cannot be aimed.
            _profile.leftEdge = left;
            _profile.rightEdge = right;
            Moved?.Invoke();
        }

        /// <summary>Put the handles back where the profile says the edges are.</summary>
        /// <remarks>
        /// Used to undo a bad drag. Without it the only way back from a handle
        /// flung across the room is redoing the whole calibration.
        /// </remarks>
        public void Resync()
        {
            if (_profile == null) return;
            // Through world space, for the same reason Update reads that way.
            if (_left != null) _left.position = ToWorld(_profile.leftEdge);
            if (_right != null) _right.position = ToWorld(_profile.rightEdge);
        }

        Vector3 ToWorld(Vector3 inSpace) =>
            _space != null ? _space.TransformPoint(inSpace) : inSpace;

        Transform NewHandle(string name, Vector3 localPosition, Material material, Transform parent)
        {
            var handle = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            handle.name = name;
            handle.transform.SetParent(parent, false);
            handle.transform.localPosition = localPosition;
            handle.transform.localScale = Vector3.one * sizeMetres;

            if (material != null && handle.TryGetComponent<Renderer>(out var renderer))
            {
                renderer.sharedMaterial = material;
            }

            // AddComponent would supply the default Rigidbody: dynamic, with
            // gravity. The handle would drop through the keyboard on the first
            // frame, taking the calibration with it.
            var body = handle.AddComponent<Rigidbody>();
            body.isKinematic = true;
            body.useGravity = false;

            var grab = handle.AddComponent<XRGrabInteractable>();

            // NOT the panel's force-grab. The panel is a window you want brought
            // to you; a handle marks a physical point on the instrument, and
            // snapping it to your hand destroys the exact value being adjusted —
            // a ray-grab from the seat wrote an edge 0.55 m across and 0.79 m up,
            // which is where a raised hand is, and saved it as the keyboard.
            grab.farAttachMode = InteractableFarAttachMode.Far;

            // Instantaneous, NOT Kinematic.
            //
            // Kinematic drives the pose through Rigidbody.MovePosition, which
            // makes physics the owner of where the handle is — and on release it
            // reasserts the pose it had before the grab. The device log caught it
            // exactly: the profile tracked the hand out to (0.62, 0.77, 0.22)
            // while the handle came back at (0.83, 0.03, -0.10), a hair from the
            // value on disk. Instantaneous writes the transform directly, so the
            // handle stays where it was put and there is no physics round trip to
            // undo it.
            grab.movementType = XRBaseInteractable.MovementType.Instantaneous;
            // A calibration point has no meaningful orientation, and letting it
            // spin makes the sphere's position harder to judge, not easier.
            grab.trackRotation = false;
            // This is a point being placed, not an object being thrown: it must
            // stay exactly where the hand left it.
            grab.throwOnDetach = false;
            // Grab it where it was actually grabbed, so the handle does not jump
            // to the centre of the hand the moment it is picked up.
            grab.useDynamicAttach = true;
            grab.selectEntered.AddListener(_ =>
                Debug.Log($"[handles] {name} grabbed inSpace "
                        + $"{(_space != null ? _space.InverseTransformPoint(handle.transform.position) : handle.transform.position):F4} "
                        + $"parented={(handle.transform.parent != null ? handle.transform.parent.name : "<none>")}"));
            grab.selectExited.AddListener(_ =>
            {
                // Read straight from the transform, and from the profile, so a
                // disagreement between the two is visible rather than inferred.
                Debug.Log($"[handles] {name} released: inSpace={(_space != null ? _space.InverseTransformPoint(handle.transform.position) : handle.transform.position):F4} "
                        + $"parented={(handle.transform.parent != null ? handle.transform.parent.name : "<none>")} "
                        + $"profileLeft={_profile?.leftEdge.ToString("F4")} "
                        + $"profileRight={_profile?.rightEdge.ToString("F4")} "
                        + $"up={_profile?.up.ToString("F3")} "
                        + $"tilt={(_profile != null ? _profile.TiltDegrees.ToString("F1") : "-")}");
                Released?.Invoke();
            });

            return handle.transform;
        }
    }
}
