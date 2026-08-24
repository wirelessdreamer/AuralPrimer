// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Writer/reader agreement for the .auralperf format:
//
//   Unity.exe -quit -batchmode -nographics \
//     -projectPath "<repo>/UnityClient/Aural Primer" \
//     -executeMethod AuralPrimer.EditorTools.RecordingRoundTripCheck.Run \
//     -logFile -
//
// The failure this exists to catch is a byte-offset disagreement between
// PerformanceCapture and PerformanceReader. It cannot be caught by eye: a
// misread take still plays, it just draws a skeleton assembled from the wrong
// bytes, and the only copy of the performance is the file that no longer means
// what it did. Discovering that after a session is discovering it too late.
//
// So the head pose is given deliberately distinctive values and checked coming
// back out. A round trip through all-zero poses would pass no matter how wrong
// the offsets were, which is worse than no test at all.

using System;
using System.IO;
using System.Reflection;
using AuralPrimer.Recording;
using UnityEditor;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public static class RecordingRoundTripCheck
    {
        const int Frames = 12;

        public static void Run()
        {
            var directory = Path.Combine(Path.GetTempPath(), "auralperf-check");
            if (Directory.Exists(directory)) Directory.Delete(directory, true);

            var failures = 0;

            // A head pose nothing else in the pipeline would produce by accident.
            var position = new Vector3(1.25f, -0.5f, 3.75f);
            var rotation = new Quaternion(0.1f, 0.2f, 0.3f, 0.927f).normalized;

            var cameraObject = new GameObject("Check Camera") { tag = "MainCamera" };
            cameraObject.AddComponent<Camera>();
            cameraObject.transform.SetPositionAndRotation(position, rotation);

            var host = new GameObject("Check Capture");
            var capture = host.AddComponent<PerformanceCapture>();

            var serialized = new SerializedObject(capture);
            serialized.FindProperty("outputDirectory").stringValue = directory;
            serialized.ApplyModifiedPropertiesWithoutUndo();

            var path = capture.Begin();
            if (string.IsNullOrEmpty(path))
            {
                Debug.LogError("[check] capture did not start");
                EditorApplication.Exit(1);
                return;
            }

            // Update is private; SendMessage drives it without widening the
            // recorder's API for the sake of this check.
            for (var i = 0; i < Frames; i++) capture.SendMessage("Update");
            capture.Stop();

            var reader = PerformanceReader.Load(path);
            if (reader == null)
            {
                Debug.LogError("[check] reader could not open the file the writer just produced");
                EditorApplication.Exit(1);
                return;
            }

            failures += Check("frame count", reader.FrameCount == Frames,
                              $"wrote {Frames}, read {reader.FrameCount}");

            var head = FindChannel(reader, "head");
            var left = FindChannel(reader, "leftHand");
            var right = FindChannel(reader, "rightHand");
            failures += Check("head channel", head != null && head.count == 1, "missing or wrong size");
            failures += Check("leftHand channel", left != null && left.count == 26, $"count={left?.count}");
            failures += Check("rightHand channel", right != null && right.count == 26, $"count={right?.count}");

            var frame = reader.Read(0);
            failures += Check("frame decodes", frame != null, "Read(0) returned null");

            if (frame != null && frame.Poses.TryGetValue("head", out var poses) && poses.Length == 1)
            {
                var got = poses[0];
                failures += Check("head position survives the round trip",
                                  Vector3.Distance(got.position, position) < 1e-4f,
                                  $"wrote {position:F4}, read {got.position:F4}");
                failures += Check("head rotation survives the round trip",
                                  Quaternion.Angle(got.rotation, rotation) < 0.05f,
                                  $"wrote {rotation:F4}, read {got.rotation:F4}");
            }
            else
            {
                failures += Check("head pose present", false, "no head channel in the decoded frame");
            }

            // Every frame must be reachable, not just the first: this is what a
            // scrub does, and an offset that drifts shows up only at the end.
            var last = reader.Read(reader.FrameCount - 1);
            failures += Check("last frame decodes", last != null, "Read(last) returned null");
            if (last != null && last.Poses.TryGetValue("head", out var lastPoses) && lastPoses.Length == 1)
            {
                failures += Check("last frame's head pose is intact",
                                  Vector3.Distance(lastPoses[0].position, position) < 1e-4f,
                                  $"read {lastPoses[0].position:F4}");
            }

            // A take cut off mid-write must still play up to the cut.
            failures += CheckTruncation(path, directory);

            failures += CheckNoLifecycleCollision();

            UnityEngine.Object.DestroyImmediate(host);
            UnityEngine.Object.DestroyImmediate(cameraObject);

            if (failures == 0) Debug.Log($"[check] round trip OK: {reader.FrameCount} frames, all assertions passed");
            else Debug.LogError($"[check] {failures} assertion(s) FAILED");

            EditorApplication.Exit(failures == 0 ? 0 : 1);
        }

        /// <summary>
        /// No public API method may share a name Unity invokes by convention.
        /// </summary>
        /// <remarks>
        /// The recorder's entry point was once called Start. Unity calls Start on
        /// the first frame, so the app recorded every session end to end without
        /// anyone pressing anything, and the only symptom was takes appearing in
        /// the list. Nothing about the code looks wrong at the call site, which is
        /// why this is a test and not a comment.
        /// </remarks>
        static int CheckNoLifecycleCollision()
        {
            var reserved = new[]
            {
                "Awake", "Start", "Update", "LateUpdate", "FixedUpdate",
                "OnEnable", "OnDisable", "OnDestroy", "Reset",
            };

            var failures = 0;
            foreach (var type in new[] { typeof(PerformanceCapture), typeof(PerformancePlayback) })
            {
                foreach (var name in reserved)
                {
                    var method = type.GetMethod(name,
                        BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly,
                        null, Type.EmptyTypes, null);

                    failures += Check($"{type.Name} has no public {name}()", method == null,
                                      "Unity calls this by name — it will fire on its own");
                }
            }
            return failures;
        }

        static int CheckTruncation(string path, string directory)
        {
            var bytes = File.ReadAllBytes(path);
            var cut = Path.Combine(directory, "truncated.auralperf");
            // Lop off most of the tail, landing mid-frame rather than on a boundary.
            File.WriteAllBytes(cut, bytes[..(bytes.Length - (bytes.Length / 3))]);

            var reader = PerformanceReader.Load(cut);
            if (reader == null) return Check("truncated file still opens", false, "reader refused it");

            return Check("truncated file keeps its complete frames",
                         reader.FrameCount > 0 && reader.FrameCount < Frames,
                         $"read {reader.FrameCount} of {Frames}");
        }

        static PerformanceReader.Channel FindChannel(PerformanceReader reader, string name)
        {
            foreach (var channel in reader.Info.channels)
            {
                if (channel.name == name) return channel;
            }
            return null;
        }

        static int Check(string what, bool passed, string detail)
        {
            if (passed) { Debug.Log($"[check] OK   {what}"); return 0; }
            Debug.LogError($"[check] FAIL {what} — {detail}");
            return 1;
        }
    }
}
