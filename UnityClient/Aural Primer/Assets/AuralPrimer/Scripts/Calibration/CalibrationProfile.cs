// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// A saved keyboard calibration: which instrument, and where it is in the room.

using System;
using System.IO;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    /// <summary>
    /// Everything needed to place the overlay on a real keyboard again next
    /// session. Serialised through JsonUtility, so the fields are deliberately
    /// plain types.
    /// </summary>
    [Serializable]
    public class CalibrationProfile
    {
        public string profileName = "My keyboard";
        public int lowestPitch = 36;
        public int highestPitch = 96;

        /// <summary>Outer left edge of the lowest white key, in world space.</summary>
        public Vector3 leftEdge;
        /// <summary>Outer right edge of the highest white key.</summary>
        public Vector3 rightEdge;
        /// <summary>Up direction of the key bed — lets the lane tilt with an
        /// instrument that is not perfectly level.</summary>
        public Vector3 up = Vector3.up;

        /// <summary>How far the falling-note lane extends above the keys.</summary>
        public float laneHeightMetres = 0.6f;
        /// <summary>Lane rake toward the player, degrees from vertical.</summary>
        public float laneTiltDegrees = 20f;
        /// <summary>Note spacing multiplier — same meaning as the desktop control.</summary>
        public float spacingMultiplier = 1f;

        public bool IsCalibrated => (rightEdge - leftEdge).sqrMagnitude > 0.0001f;

        public float WidthMetres => Vector3.Distance(leftEdge, rightEdge);

        /// <summary>Left-to-right axis of the instrument, normalised.</summary>
        public Vector3 RightAxis
        {
            get
            {
                var axis = rightEdge - leftEdge;
                return axis.sqrMagnitude > 1e-6f ? axis.normalized : Vector3.right;
            }
        }

        public KeyboardLayout BuildLayout() => new(lowestPitch, highestPitch);

        /// <summary>World position of a key's centre, on the key bed.</summary>
        public Vector3 KeyPosition(KeyboardLayout layout, int pitch)
        {
            var t = (float)layout.NormalisedX(pitch);
            return Vector3.Lerp(leftEdge, rightEdge, t);
        }

        // ---- Persistence ---------------------------------------------------

        public static string PathFor(string profileName)
        {
            var safe = string.Join("_", profileName.Split(Path.GetInvalidFileNameChars()));
            return Path.Combine(Application.persistentDataPath, $"calibration_{safe}.json");
        }

        public void Save()
        {
            try
            {
                File.WriteAllText(PathFor(profileName), JsonUtility.ToJson(this, true));
                Debug.Log($"[calibration] saved {profileName}");
            }
            catch (Exception e)
            {
                Debug.LogError($"[calibration] could not save {profileName}: {e.Message}");
            }
        }

        public static CalibrationProfile Load(string profileName)
        {
            var path = PathFor(profileName);
            try
            {
                if (!File.Exists(path)) return null;
                var profile = JsonUtility.FromJson<CalibrationProfile>(File.ReadAllText(path));
                // A profile that never completed calibration is worse than none:
                // it would place the overlay at the origin and look like a bug.
                return profile is { IsCalibrated: true } ? profile : null;
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[calibration] could not load {profileName}: {e.Message}");
                return null;
            }
        }
    }
}
