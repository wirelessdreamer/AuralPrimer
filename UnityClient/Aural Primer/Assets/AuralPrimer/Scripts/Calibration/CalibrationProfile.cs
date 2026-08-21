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
        /// <summary>
        /// Which calibration scheme produced this profile.
        /// </summary>
        /// <remarks>
        /// Bumped whenever a fix changes what the stored numbers mean. A profile
        /// from an older scheme is not wrong-looking — it restores cleanly and
        /// puts the keyboard in the wrong place, which is worse than failing,
        /// because the wizard then skips calibration and the user is left with a
        /// silently misplaced overlay and nothing to act on.
        /// </remarks>
        public const int CurrentVersion = 2;

        /// <remarks>
        /// Deliberately defaults to 0, not CurrentVersion. JsonUtility only
        /// overwrites fields the JSON actually contains, so a profile written
        /// before this field existed keeps whatever the initialiser said — and
        /// an initialiser of CurrentVersion would make every old profile claim
        /// to be current, which is exactly the check failing to do its job.
        /// Absent must mean old.
        /// </remarks>
        public int version;

        public string profileName = "My keyboard";
        public int lowestPitch = 36;
        public int highestPitch = 96;

        /// <summary>
        /// Persistent spatial anchor this calibration is expressed against.
        /// </summary>
        /// <remarks>
        /// Edges used to be stored in world space, which does not survive a
        /// restart: the headset re-localises, so the same numbers describe a
        /// different place in the room. The anchor is the platform's own record
        /// of a place, re-localised against the room on load — so restoring is
        /// knowing rather than guessing.
        /// </remarks>
        public string anchorId;

        /// <summary>Outer left edge of the lowest white key, relative to the anchor.</summary>
        public Vector3 leftEdge;
        /// <summary>Outer right edge of the highest white key, relative to the anchor.</summary>
        public Vector3 rightEdge;
        /// <summary>Up direction of the key bed, relative to the anchor — lets the
        /// lane tilt with an instrument that is not perfectly level.</summary>
        public Vector3 up = Vector3.up;

        /// <summary>How far the falling-note lane extends above the keys.</summary>
        public float laneHeightMetres = 0.6f;
        /// <summary>Lane rake toward the player, degrees from vertical.</summary>
        public float laneTiltDegrees = 20f;
        /// <summary>Note spacing multiplier — same meaning as the desktop control.</summary>
        public float spacingMultiplier = 1f;

        public bool IsCalibrated => (rightEdge - leftEdge).sqrMagnitude > 0.0001f;

        /// <summary>Calibrated and tied to an anchor that can be re-localised.</summary>
        public bool IsAnchored =>
            IsCalibrated && !string.IsNullOrEmpty(anchorId) && version == CurrentVersion;

        /// <summary>
        /// Re-express world-space edges against an anchor.
        /// </summary>
        public void RebaseOnto(Transform anchorSpace, Vector3 worldLeft, Vector3 worldRight, Vector3 worldUp)
        {
            if (anchorSpace == null)
            {
                leftEdge = worldLeft;
                rightEdge = worldRight;
                up = worldUp;
                return;
            }

            leftEdge = anchorSpace.InverseTransformPoint(worldLeft);
            rightEdge = anchorSpace.InverseTransformPoint(worldRight);
            up = anchorSpace.InverseTransformDirection(worldUp);
        }

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

        /// <summary>A key's centre on the key bed, in the anchor's space.</summary>
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
            // Stamp on the way out: anything written now is by definition current.
            version = CurrentVersion;
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
