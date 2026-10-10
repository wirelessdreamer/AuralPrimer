// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Where the neck sits relative to the controller mounted on the guitar.
//
// The printed mount fixes the controller to the head end, so the neck frame
// (see FretboardGeometry) is a fixed offset from the controller's tracked grip
// pose. The CAD gives a first guess at that offset; the alignment check in
// setup measures the real one and replaces it.

using System;
using System.Collections.Generic;
using System.Numerics;
using AuralPrimer.Link;

namespace AuralPrimer.Calibration
{
    /// <summary>The neck frame expressed in the controller's grip-pose frame (Unity axes).</summary>
    public readonly struct MountOffset
    {
        /// <summary>The neck's origin (nut centre) in the grip frame, metres.</summary>
        public readonly Vector3 Position;
        /// <summary>The neck's axes in the grip frame: rotates neck-local into grip-local.</summary>
        public readonly Quaternion Rotation;

        public MountOffset(Vector3 position, Quaternion rotation)
        {
            Position = position;
            Rotation = Quaternion.Normalize(rotation);
        }

        /// <summary>
        /// The CAD's first guess, from hardware/quest-guitar-mount/mount_offset.json.
        /// </summary>
        /// <remarks>
        /// Copied from that file, which build_mount.py::mount_offset writes with
        /// Meta's real Touch Plus model seated in the cradle. Its reference is
        /// that model's origin and axes (MetaQuestTouchPlus_Left as Unity draws
        /// it, where the Meta SDK's OVRControllerPrefab puts it). GuitarAnchor
        /// applies it to the controller's tracked grip pose; whether that pose is
        /// exactly where the SDK draws its model is unchecked on a headset. The
        /// alignment check replaces it with a measurement, so being centimetres
        /// out here costs a nudge, not a wrong neck. When the mount is rebuilt,
        /// either update these numbers or hand the new file to GuitarAnchor,
        /// which reads the same shape (<see cref="TryParse"/>).
        /// </remarks>
        public static MountOffset Cad => new(
            new Vector3(-0.0101f, -0.0882f, -0.1405f),
            new Quaternion(0.63318f, 0.29008f, -0.69843f, 0.16469f));

        /// <summary>
        /// Read the mount's offset file:
        /// <c>{"neck_origin_in_grip_m":[x,y,z],"neck_rotation_in_grip_quat":[x,y,z,w], ...}</c>.
        /// </summary>
        public static bool TryParse(string json, out MountOffset offset)
        {
            offset = Cad;
            object root;
            try
            {
                root = MiniJson.Parse(json);
            }
            catch (FormatException)
            {
                return false;
            }
            if (root is not Dictionary<string, object> obj) return false;
            if (!TryFloats(obj, "neck_origin_in_grip_m", 3, out var p)) return false;
            if (!TryFloats(obj, "neck_rotation_in_grip_quat", 4, out var q)) return false;

            var rotation = new Quaternion(q[0], q[1], q[2], q[3]);
            // A zero quaternion is no rotation at all, and normalising it is NaN.
            if (rotation.LengthSquared() < 1e-6f) return false;

            offset = new MountOffset(new Vector3(p[0], p[1], p[2]), rotation);
            return true;
        }

        static bool TryFloats(Dictionary<string, object> obj, string key, int count, out float[] values)
        {
            values = null;
            if (!obj.TryGetValue(key, out var raw) || raw is not List<object> list || list.Count != count) return false;
            values = new float[count];
            for (var i = 0; i < count; i++)
            {
                if (list[i] is not double d) return false;
                values[i] = (float)d;
            }
            return true;
        }
    }
}
