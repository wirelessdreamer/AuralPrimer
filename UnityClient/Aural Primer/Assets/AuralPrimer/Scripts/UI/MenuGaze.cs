// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Where the menus are, so a ray can know whether it is wanted.
//
// Registered rather than searched: a ray asks this question every frame from
// both hands, and FindObjectsByType at that rate is a cost paid forever to
// rediscover something the panel already knew when it was built.
//
// A rectangle and a transform rather than a Collider, which was the obvious
// thing and the wrong one. The panel's only colliders are its two drag bars --
// the grab target is the handle alone, on purpose, because a panel that moves
// wherever you touch it is a panel whose buttons you cannot press. Registering
// those would answer "is the head on the MOVE bar", not "on the menu"; adding a
// third collider over the face would be collected by the XRGrabInteractable
// sitting on the same object and make the whole panel draggable again. So the
// face is described directly, and nothing about the grab behaviour changes.

using System.Collections.Generic;
using UnityEngine;

namespace AuralPrimer.UI
{
    public static class MenuGaze
    {
        readonly struct Panel
        {
            public readonly Transform Face;
            public readonly Vector2 HalfExtents;

            public Panel(Transform face, Vector2 halfExtents)
            {
                Face = face;
                HalfExtents = halfExtents;
            }
        }

        static readonly List<Panel> _menus = new();

        /// <summary>
        /// A panel that counts as somewhere worth pointing.
        /// </summary>
        /// <param name="face">Origin at the panel's centre, +Z out of its face.</param>
        /// <param name="size">Width and height of the face, in metres.</param>
        public static void Register(Transform face, Vector2 size)
        {
            if (face == null) return;
            Unregister(face);
            _menus.Add(new Panel(face, size * 0.5f));
        }

        public static void Unregister(Transform face)
        {
            for (var i = _menus.Count - 1; i >= 0; i--)
            {
                if (_menus[i].Face == face) _menus.RemoveAt(i);
            }
        }

        /// <summary>Does this ray land on a menu within <paramref name="maxDistance"/>?</summary>
        /// <remarks>
        /// Destroyed and hidden panels are skipped, and destroyed ones dropped as
        /// they are found: a panel torn down without unregistering would
        /// otherwise leave a dead entry that every future query steps over.
        /// </remarks>
        public static bool IsLookedAt(Ray ray, float maxDistance)
        {
            for (var i = _menus.Count - 1; i >= 0; i--)
            {
                var menu = _menus[i];
                if (menu.Face == null)
                {
                    _menus.RemoveAt(i);
                    continue;
                }
                if (!menu.Face.gameObject.activeInHierarchy) continue;
                if (HitsFace(menu, ray, maxDistance)) return true;
            }
            return false;
        }

        /// <summary>Where the ray crosses the panel's plane, in the panel's own space.</summary>
        /// <remarks>
        /// Solved in local space so the panel can hang at any angle: the menu
        /// swings on a hinge and tilts on its top bar, so an axis-aligned test in
        /// world space would only be right while it happened to face down an
        /// axis. Both facings count -- looking at the back of a panel is still
        /// looking at the panel, and refusing the ray there would leave a player
        /// who has swung the menu past square unable to point at it.
        /// </remarks>
        static bool HitsFace(Panel menu, Ray ray, float maxDistance)
        {
            var origin = menu.Face.InverseTransformPoint(ray.origin);
            var direction = menu.Face.InverseTransformDirection(ray.direction);

            // Parallel to the face: it is being looked along, not at.
            if (Mathf.Abs(direction.z) < 1e-5f) return false;

            var t = -origin.z / direction.z;
            if (t < 0f || t > maxDistance) return false;

            var hit = origin + direction * t;
            return Mathf.Abs(hit.x) <= menu.HalfExtents.x
                && Mathf.Abs(hit.y) <= menu.HalfExtents.y;
        }
    }
}
