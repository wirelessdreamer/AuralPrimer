// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// What happens to the player's hands where they cross the drawn keys.
//
// The overlay is a row of plates a few millimetres above the real keys, drawn
// after the passthrough image — so a hand reaching for a note goes behind it
// and vanishes exactly where the player is trying to aim.
//
// Both cures use the same tracked hand mesh and differ only in the material
// hung on it, because the whole problem is a depth-write problem:
//
//   Rendered  an opaque, lit hand. Writes depth, so the keys behind it are
//             hidden. You see a model of your hand.
//   Occluded  the same mesh with colour writes off. Writes depth and nothing
//             else, so the app draws nothing over those pixels and the
//             compositor shows your real hand through the hole.
//
// The sample's own HandsDefaultMaterial is transparent with ZWrite off, which
// is why the first attempt let the keys show straight through the hands: a mesh
// that writes no depth cannot hide anything.

using System.Collections.Generic;
using Unity.XR.CoreUtils;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Hand Visuals")]
    public sealed class HandVisuals : MonoBehaviour
    {
        /// <summary>Resources paths for the tracked-hand prefabs.</summary>
        /// <remarks>
        /// Loaded by path rather than serialised so this component can be added
        /// at runtime like the rest of the wizard's pieces, with no scene edit
        /// to lose. The prefabs are copies of the XR Hands sample's, which is
        /// committed for exactly this reason.
        /// </remarks>
        const string LeftHandResource = "Hands/LeftHandTracking";
        const string RightHandResource = "Hands/RightHandTracking";

        /// <summary>An opaque hand you can see. Ships with the XR Hands sample.</summary>
        const string VisibleMaterialResource = "Hands/HandVisible";

        /// <summary>Depth only, no colour — the cut-out.</summary>
        const string OcclusionMaterialResource = "Hands/HandOcclusion";

        readonly List<GameObject> _hands = new();
        CalibrationProfile.HandVisual _applied = (CalibrationProfile.HandVisual)(-1);

        /// <summary>Put the chosen treatment into effect.</summary>
        /// <remarks>
        /// Idempotent, and cheap to call on every profile refresh: it returns
        /// immediately unless the mode actually changed. That matters because
        /// the wizard reapplies the profile on every edge drag.
        /// </remarks>
        public void Apply(CalibrationProfile profile)
        {
            var wanted = profile != null ? profile.handVisual : CalibrationProfile.HandVisual.Overlay;
            if (wanted == _applied) return;
            _applied = wanted;

            Rebuild(wanted);
            Debug.Log($"[hands] mode {wanted} ({_hands.Count} hand object(s))");
        }

        void OnDestroy() => Rebuild(CalibrationProfile.HandVisual.Overlay);

        void Rebuild(CalibrationProfile.HandVisual mode)
        {
            foreach (var hand in _hands)
            {
                if (hand != null) Destroy(hand);
            }
            _hands.Clear();

            if (mode == CalibrationProfile.HandVisual.Overlay) return;

            var material = Resources.Load<Material>(
                mode == CalibrationProfile.HandVisual.Occluded
                    ? OcclusionMaterialResource
                    : VisibleMaterialResource);

            if (material == null)
            {
                // Missing art is a build problem, not a runtime one: say so
                // plainly rather than leaving the mode silently doing nothing,
                // which is how the last set of ignored-but-required assets went
                // unnoticed until it shipped.
                Debug.LogWarning($"[hands] no material for {mode} — falling back to the overlay");
                return;
            }

            var origin = FindAnyObjectByType<XROrigin>();
            var parent = origin != null ? origin.transform : null;

            foreach (var path in new[] { LeftHandResource, RightHandResource })
            {
                var prefab = Resources.Load<GameObject>(path);
                if (prefab == null)
                {
                    Debug.LogWarning($"[hands] no prefab at Resources/{path} — "
                                   + "this hand is unavailable in this build");
                    continue;
                }

                // Parented to the XR origin's tracking space, not to us: hand
                // joints are reported in that space, and a rig hung anywhere
                // else drifts with whatever it was attached to.
                var hand = Instantiate(prefab, parent);
                hand.name = $"{prefab.name} ({mode})";
                Wear(hand, material);
                _hands.Add(hand);
            }
        }

        /// <summary>Put one material on every renderer under a hand.</summary>
        /// <remarks>
        /// Every renderer, including the ones inside the rig: the sample's
        /// prefabs carry a skinned mesh plus optional joint debug spheres, and
        /// leaving any of them on the sample's transparent material would poke a
        /// visible, non-occluding hole through the effect.
        /// </remarks>
        static void Wear(GameObject hand, Material material)
        {
            foreach (var renderer in hand.GetComponentsInChildren<Renderer>(true))
            {
                var slots = renderer.sharedMaterials;
                for (var i = 0; i < slots.Length; i++) slots[i] = material;
                renderer.sharedMaterials = slots;
            }
        }
    }
}
