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

        /// <summary>
        /// Gap between the real keys and the "play now" line, in metres.
        /// </summary>
        /// <remarks>
        /// The line used to sit 4 mm off the key bed, which reads as buried in
        /// the keys rather than hovering over them: at that distance passthrough
        /// gives no parallax to separate the two, so an arriving note looks like
        /// it is inside the keyboard.
        /// </remarks>
        public float laneLiftMetres = 0.05f;

        /// <summary>
        /// Roll of the key plane about the instrument's own axis, in degrees.
        /// </summary>
        /// <remarks>
        /// Two pinched points fix a line, not a plane — the roll around that line
        /// is unconstrained, so the drawn keys can sit canted against the real
        /// ones with the span and position both perfectly correct. This is the
        /// missing degree of freedom, exposed rather than guessed.
        /// </remarks>
        public float keyCantDegrees;

        /// <summary>Largest cant worth offering; beyond it the marks are wrong.</summary>
        public const float MaxCantDegrees = 15f;

        /// <summary>
        /// Drop notes the instrument physically cannot play.
        /// </summary>
        /// <remarks>
        /// A chart can range wider than the keyboard in front of you. Off, those
        /// notes are folded into range by whole octaves so the line stays
        /// playable; on, they are simply not drawn, which is honest about the
        /// part being incomplete rather than quietly transposing it.
        /// </remarks>
        public bool ignoreOutOfRangeNotes = true;

        /// <summary>How the player's real hands are treated over the keys.</summary>
        /// <remarks>
        /// The drawn keys are opaque plates a few millimetres above the real
        /// ones, so a hand passing over them disappears behind the overlay --
        /// you lose your own fingers exactly where you are trying to place
        /// them. Neither answer is right for everyone, so this is a choice.
        /// </remarks>
        public enum HandVisual
        {
            /// <summary>Draw the keys over everything, hands included.</summary>
            Overlay = 0,

            /// <summary>Draw tracked hand meshes on top of the keys.</summary>
            Rendered = 1,

            /// <summary>Let the real hands occlude the keys via scene depth.</summary>
            Occluded = 2,
        }

        /// <summary>
        /// Which hand treatment to use. Defaults to what the app always did.
        /// </summary>
        /// <remarks>
        /// Serialised as its underlying int by JsonUtility, so an older profile
        /// with no value at all loads as Overlay -- the previous behaviour --
        /// rather than as something the player never chose.
        /// </remarks>
        public HandVisual handVisual = HandVisual.Overlay;

        /// <summary>
        /// Colour falling notes by which note they are, rather than by how
        /// close they are.
        /// </summary>
        /// <remarks>
        /// On by default, unlike the other display choices. The approach
        /// spectrum this replaces was saying something the lane already says
        /// better -- a note's height above the keys IS its timing, read as
        /// geometry without being taught -- so hue was the weaker duplicate of
        /// an encoding already present. Spending it on identity costs nothing.
        ///
        /// JsonUtility gives an absent bool false, so an older profile would
        /// load as OFF and quietly disagree with a new one. Stored inverted for
        /// that reason: the field records the departure from the default, and
        /// the property reads the way the rest of the code wants it.
        /// </remarks>
        public bool noteColorsDisabled;

        /// <summary>Colour notes by pitch class on the lane.</summary>
        public bool NoteColors
        {
            get => !noteColorsDisabled;
            set => noteColorsDisabled = !value;
        }

        /// <summary>Which end of the keyboard the menu hangs off.</summary>
        public bool menuOnHighEnd = true;

        /// <summary>
        /// Swing of the menu about its hinge, in degrees.
        /// </summary>
        /// <remarks>
        /// The menu is pinned by its inner vertical edge to the corner of the
        /// instrument and swings like a door. Zero is straight out along the key
        /// bed; positive swings the outer edge toward the player.
        /// </remarks>
        public float menuYawDegrees = 35f;

        /// <summary>The octave-folded pitch, or -1 when it should be dropped.</summary>
        public int FoldPitch(KeyboardLayout layout, int pitch)
        {
            if (layout.Contains(pitch)) return pitch;
            if (ignoreOutOfRangeNotes) return -1;

            // Whole octaves only: anything else changes which note it is.
            var folded = pitch;
            while (folded < layout.LowestPitch) folded += 12;
            while (folded > layout.HighestPitch) folded -= 12;
            return layout.Contains(folded) ? folded : -1;
        }

        /// <summary>The key bed's up axis with the cant applied.</summary>
        public Vector3 CantedUp
        {
            get
            {
                var level = up.sqrMagnitude > 1e-6f ? up.normalized : Vector3.up;
                return Mathf.Abs(keyCantDegrees) < 1e-3f
                    ? level
                    : Quaternion.AngleAxis(keyCantDegrees, RightAxis) * level;
            }
        }

        /// <summary>
        /// How far the key bed tilts off level, in degrees.
        /// </summary>
        /// <remarks>
        /// A real keyboard is level to within a degree or two even on a wobbly
        /// stand. Large values mean the two edges were not both captured on the
        /// instrument.
        /// </remarks>
        public float TiltDegrees
        {
            get
            {
                var axis = rightEdge - leftEdge;
                if (axis.sqrMagnitude < 1e-6f) return 0f;
                var level = up.sqrMagnitude > 1e-6f ? up.normalized : Vector3.up;
                // 90 degrees from the up axis is level; anything else is rake.
                return Mathf.Abs(90f - Vector3.Angle(axis, level));
            }
        }

        /// <summary>Beyond this, the marks describe something that is not a keyboard.</summary>
        public const float MaxTiltDegrees = 10f;

        public bool IsCalibrated => (rightEdge - leftEdge).sqrMagnitude > 0.0001f;

        /// <summary>
        /// Calibrated, and level enough to actually be an instrument.
        /// </summary>
        /// <remarks>
        /// Width alone does not catch a bad edge. A right edge captured at head
        /// height still measured 0.97 m from the left one — a plausible span for
        /// 61 keys — so the width check passed it and the overlay drew a
        /// staircase climbing 79 cm across the instrument. The span was right;
        /// the direction was not.
        /// </remarks>
        public bool IsPlausible => IsCalibrated && TiltDegrees <= MaxTiltDegrees;

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
                if (profile is not { IsCalibrated: true }) return null;

                // Nor is a saved-but-impossible one worth restoring. Silently
                // re-running the wizard beats drawing a keyboard that climbs into
                // the ceiling and leaving the player to work out why.
                if (!profile.IsPlausible)
                {
                    Debug.LogWarning($"[calibration] {profileName} tilts "
                                   + $"{profile.TiltDegrees:F0}° off level — discarding it "
                                   + "and asking for the edges again");
                    return null;
                }

                return profile;
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[calibration] could not load {profileName}: {e.Message}");
                return null;
            }
        }
    }
}
