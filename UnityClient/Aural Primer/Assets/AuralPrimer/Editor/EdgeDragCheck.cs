// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Does a dragged calibration edge actually stay where it was put?
//
//   Unity.exe -quit -batchmode -nographics \
//     -projectPath "<repo>/UnityClient/Aural Primer" \
//     -executeMethod AuralPrimer.EditorTools.EdgeDragCheck.Run \
//     -logFile -
//
// The reported symptom is a handle springing back to its pre-grab position on
// release. There are only two things in the client that write a handle back to
// the stored edge — the plausibility rejection and Show() — so this walks a
// realistic drag through the same arithmetic and prints which one fires.

using AuralPrimer.Calibration;
using UnityEditor;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public static class EdgeDragCheck
    {
        public static void Run()
        {
            var failures = 0;

            // A 61-key bed a metre wide, level, as a real calibration produces.
            var left = new Vector3(-0.5f, 0f, 0.4f);
            var right = new Vector3(0.5f, 0f, 0.4f);

            var profile = new CalibrationProfile
            {
                profileName = "DragCheck",
                lowestPitch = 36,
                highestPitch = 96,
                leftEdge = left,
                rightEdge = right,
                up = Vector3.up,
            };

            Debug.Log($"[drag] level bed: tilt={profile.TiltDegrees:F2}° "
                    + $"plausible={profile.IsPlausible} span={profile.WidthMetres:F3}m");
            failures += Check("a level bed is plausible", profile.IsPlausible, "rejected at rest");

            // Realistic hand drags: a few centimetres, with the vertical wobble a
            // hand actually has. None of these should be refused.
            foreach (var (name, delta) in new[]
                     {
                         ("slide 3cm along the keys", new Vector3(0.03f, 0f, 0f)),
                         ("3cm out, 1cm up", new Vector3(0.03f, 0.01f, 0f)),
                         ("5cm out, 2cm up", new Vector3(0.05f, 0.02f, 0f)),
                         ("2cm toward the player", new Vector3(0f, 0f, -0.02f)),
                     })
            {
                profile.leftEdge = left + delta;
                var tilt = profile.TiltDegrees;
                var ok = profile.IsPlausible;
                Debug.Log($"[drag] {name,-26} tilt={tilt,6:F2}°  plausible={ok}");
                failures += Check($"drag survives: {name}", ok,
                                  $"tilt {tilt:F2}° exceeded {CalibrationProfile.MaxTiltDegrees}°");
            }

            // And the failure that started all this must still be caught.
            profile.leftEdge = left;
            profile.rightEdge = new Vector3(-0.046f, 0.79f, 0.558f);
            Debug.Log($"[drag] the airborne edge: tilt={profile.TiltDegrees:F2}° "
                    + $"plausible={profile.IsPlausible}");
            failures += Check("an edge at head height is still refused", !profile.IsPlausible,
                              "the 79cm-high edge would be accepted again");

            // Now the handle round trip: move the transform, confirm the profile
            // takes the value, exactly as EdgeHandles.Update does it.
            profile.leftEdge = left;
            profile.rightEdge = right;

            var host = new GameObject("Drag Host");
            var space = new GameObject("Anchor Space").transform;
            // A non-identity anchor, because a real spatial anchor is never at
            // the origin and never axis-aligned.
            space.SetPositionAndRotation(new Vector3(1.4f, 0.9f, -2.1f),
                                         Quaternion.Euler(0f, 37f, 0f));

            var handles = host.AddComponent<EdgeHandles>();
            var moved = 0;
            handles.Moved += () => moved++;
            handles.Show(profile, space, null, null);

            var handle = space.Find("Left Edge Handle");
            failures += Check("handles are built", handle != null, "no Left Edge Handle");

            if (handle != null)
            {
                failures += Check("handle starts on the stored edge",
                                  Vector3.Distance(handle.localPosition, left) < 1e-5f,
                                  $"started at {handle.localPosition:F4}, stored {left:F4}");

                // Drag it 4 cm along the keys, in WORLD space — which is how a
                // hand moves it, through a rotated parent.
                var target = handle.position + space.right * 0.04f;
                handle.position = target;
                var expected = handle.localPosition;

                handles.SendMessage("Update");

                failures += Check("Moved fired", moved > 0, "no Moved event");
                failures += Check("profile took the dragged value",
                                  Vector3.Distance(profile.leftEdge, expected) < 1e-5f,
                                  $"profile {profile.leftEdge:F4}, handle {expected:F4}");
                failures += Check("the dragged result is still plausible",
                                  profile.IsPlausible,
                                  $"tilt {profile.TiltDegrees:F2}° — release would snap it back");
            }

            Object.DestroyImmediate(host);
            Object.DestroyImmediate(space.gameObject);

            if (failures == 0) Debug.Log("[drag] all assertions passed");
            else Debug.LogError($"[drag] {failures} assertion(s) FAILED");
            EditorApplication.Exit(failures == 0 ? 0 : 1);
        }

        static int Check(string what, bool passed, string detail)
        {
            if (passed) { Debug.Log($"[drag] OK   {what}"); return 0; }
            Debug.LogError($"[drag] FAIL {what} — {detail}");
            return 1;
        }
    }
}
