// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Where the keyboard is, published for interactors that must keep off it.
//
// The hand ray needs to know when a hand is over the keys so it can put itself
// away, but that knowledge lives in the calibration, and AuralPrimer.Calibration
// already references AuralPrimer.UI — reaching back the other way is a circular
// assembly dependency, which Unity refuses outright.
//
// So the fact is pushed here instead of pulled from there. It is deliberately
// static: there is exactly one keyboard in a session, no scene object needs to
// exist for an interactor to ask about it, and an interactor that runs before
// calibration finishes gets an honest "unknown" rather than a null reference.

using UnityEngine;

namespace AuralPrimer.UI
{
    public static class KeyboardProximity
    {
        static Vector3 _left;
        static Vector3 _right;
        static Vector3 _depth;
        static bool _known;

        public const float RayHideDistanceMetres = 0.3048f; // one foot
        public const float RayShowDistanceMetres = 0.36f;

        /// <summary>Publish the calibrated key bed, in world space.</summary>
        public static void Publish(Vector3 worldLeft, Vector3 worldRight, Vector3 worldDepth = default)
        {
            _left = worldLeft;
            _right = worldRight;
            _depth = worldDepth;
            _known = true;
        }

        /// <summary>Forget it — no calibration, so no keys to stay off.</summary>
        public static void Clear() => _known = false;

        /// <summary>
        /// Whether rays should get out of the way over the keys at all.
        /// </summary>
        /// <remarks>
        /// Off while calibrating. The edge handles sit ON the key bed, so the
        /// rule that hides a ray near the keys also hides the only means of
        /// pointing at them — leaving a 3.5 cm sphere reachable solely by
        /// physically touching it, which reads as "there is no grab handle".
        /// </remarks>
        public static bool SuppressOverKeys { get; set; } = true;

        /// <summary>
        /// Distance from a world point to the key bed, or -1 if unknown.
        /// </summary>
        /// <remarks>
        /// Measured to the whole key bed, including its depth toward the player.
        /// The published width and depth axes are perpendicular. This stays
        /// correct while an edge is dragged or the spatial anchor is rotated.
        /// </remarks>
        public static float Distance(Vector3 worldPoint)
        {
            if (!_known) return -1f;

            var axis = _right - _left;
            var lengthSquared = axis.sqrMagnitude;
            var t = lengthSquared < 1e-6f ? 0f
                : Mathf.Clamp01(Vector3.Dot(worldPoint - _left, axis) / lengthSquared);
            var closest = _left + axis * t;
            var depthSquared = _depth.sqrMagnitude;
            if (depthSquared >= 1e-6f)
                closest += _depth * Mathf.Clamp01(Vector3.Dot(worldPoint - closest, _depth) / depthSquared);
            return Vector3.Distance(worldPoint, closest);
        }

        /// <summary>Hide within a foot; allow a little extra clearance before showing again.</summary>
        /// <param name="wasHidden">This hand's previous proximity state, independent of the other hand.</param>
        public static bool ShouldHideRay(float distanceMetres, bool wasHidden) =>
            SuppressOverKeys && distanceMetres >= 0f &&
            distanceMetres <= (wasHidden ? RayShowDistanceMetres : RayHideDistanceMetres);
    }
}
