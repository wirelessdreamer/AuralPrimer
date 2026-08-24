// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// A drag bar across the top of the note display.
//
// Grab it and move it: where you put it IS the lane. Its distance from the keys
// sets how tall the display is, and how far it leans toward you sets the rake.
// Both come out of one gesture, because they are one thing — the plane you read
// the falling notes off — and splitting them into two pairs of stepper buttons
// asks the player to solve by arithmetic what they can just reach out and set.

using System;
using UnityEngine;
using UnityEngine.XR.Interaction.Toolkit.Attachment;
using UnityEngine.XR.Interaction.Toolkit.Interactables;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Lane Handle")]
    public sealed class LaneHandle : MonoBehaviour
    {
        [Tooltip("Thickness of the bar, in metres.")]
        [SerializeField] float barThicknessMetres = 0.022f;

        [Tooltip("How much of the keyboard's width the bar spans.")]
        [SerializeField] float barWidthFraction = 0.5f;

        /// <summary>Raised while the bar is being dragged.</summary>
        public event Action Moved;

        /// <summary>Raised when it is let go.</summary>
        public event Action Released;

        CalibrationProfile _profile;
        Transform _space;
        Transform _bar;

        public bool IsShowing => _bar != null;

        /// <summary>Put the bar at the top of the lane the profile describes.</summary>
        public void Show(CalibrationProfile profile, Transform space, Material material)
        {
            if (profile == null || !profile.IsCalibrated) { Hide(); return; }
            if (IsShowing && _profile == profile) return;

            Hide();
            _profile = profile;
            _space = space != null ? space : transform;

            var bar = GameObject.CreatePrimitive(PrimitiveType.Cube);
            bar.name = "Lane Drag Bar";
            bar.transform.SetParent(_space, false);

            if (material != null && bar.TryGetComponent<Renderer>(out var renderer))
            {
                renderer.sharedMaterial = material;
            }

            var body = bar.AddComponent<Rigidbody>();
            body.isKinematic = true;
            body.useGravity = false;

            var grab = bar.AddComponent<XRGrabInteractable>();
            // Far, not Near: this marks a place in the room. Snapping it to the
            // hand on a distance grab would destroy the angle being set, the same
            // way it wrecked a calibration edge.
            grab.farAttachMode = InteractableFarAttachMode.Far;
            // Instantaneous, so the transform is written directly and physics
            // never gets to reassert the pose on release.
            grab.movementType = XRBaseInteractable.MovementType.Instantaneous;
            grab.trackRotation = false;
            grab.throwOnDetach = false;
            grab.useDynamicAttach = true;
            // Name the interactor on both edges: "grabbed but will not release"
            // is almost always the wrong interactor holding it, and that is
            // invisible unless it is said out loud.
            grab.selectEntered.AddListener(a =>
                Debug.Log($"[lane] grabbed by {a.interactorObject?.transform?.name ?? "<unknown>"}"));
            grab.selectExited.AddListener(a =>
            {
                Debug.Log($"[lane] released by {a.interactorObject?.transform?.name ?? "<unknown>"} "
                        + $"(cancelled={a.isCanceled})");
                Released?.Invoke();
            });

            _bar = bar.transform;
            Place();
        }

        public void Hide()
        {
            if (_bar != null) Destroy(_bar.gameObject);
            _bar = null;
            _profile = null;
        }

        void OnDestroy() => Hide();

        void Update()
        {
            if (_profile == null || _bar == null) return;

            // World, converted into the calibration's space — never
            // localPosition, because XRI unparents an interactable while it is
            // held and localPosition silently becomes world coordinates.
            var inSpace = _space.InverseTransformPoint(_bar.position);

            var centre = Vector3.Lerp(_profile.leftEdge, _profile.rightEdge, 0.5f);
            var offset = inSpace - centre;
            if (offset.sqrMagnitude < 1e-6f) return;

            var up = Up();
            var face = Face();

            // The inverse of how the lane is built: laneUp = up*cos(t) + face*sin(t).
            var alongUp = Vector3.Dot(offset, up);
            var alongFace = Vector3.Dot(offset, face);

            // Wide enough to actually be a free hand.
            //
            // 0 degrees is straight up, 90 is flat toward the player, past 90 is
            // leaning beyond horizontal, negative leans away. Clamping to 0..70
            // meant the bar simply stopped following the hand — the display
            // could not be laid back past 70, never mind past 90.
            var height = Mathf.Clamp(offset.magnitude, 0.08f, 2.0f);
            var tilt = Mathf.Clamp(Mathf.Atan2(alongFace, alongUp) * Mathf.Rad2Deg, -60f, 150f);

            if (Mathf.Abs(height - _profile.laneHeightMetres) < 1e-4f
                && Mathf.Abs(tilt - _profile.laneTiltDegrees) < 1e-3f)
            {
                return;
            }

            _profile.laneHeightMetres = height;
            _profile.laneTiltDegrees = tilt;
            Moved?.Invoke();
        }

        /// <summary>Put the bar back where the profile says the lane's top is.</summary>
        public void Place()
        {
            if (_profile == null || _bar == null) return;

            var centre = Vector3.Lerp(_profile.leftEdge, _profile.rightEdge, 0.5f);
            var tilt = _profile.laneTiltDegrees * Mathf.Deg2Rad;
            var laneUp = (Up() * Mathf.Cos(tilt) + Face() * Mathf.Sin(tilt)).normalized;

            _bar.position = _space.TransformPoint(centre + laneUp * _profile.laneHeightMetres);
            _bar.rotation = _space.rotation * Quaternion.LookRotation(laneUp, Vector3.Cross(_profile.RightAxis, laneUp));
            _bar.localScale = new Vector3(
                _profile.WidthMetres * barWidthFraction, barThicknessMetres, barThicknessMetres);
        }

        Vector3 Up() =>
            _profile.up.sqrMagnitude > 1e-6f ? _profile.up.normalized : Vector3.up;

        /// <summary>Out the front of the instrument, toward the player.</summary>
        Vector3 Face()
        {
            var face = Vector3.Cross(_profile.RightAxis, Up()).normalized;

            var head = Camera.main;
            if (head == null) return face;

            var centre = Vector3.Lerp(_profile.leftEdge, _profile.rightEdge, 0.5f);
            var toPlayer = _space.InverseTransformPoint(head.transform.position) - centre;
            return Vector3.Dot(face, toPlayer) < 0f ? -face : face;
        }
    }
}
