// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Headless APK build, so a build does not require the Editor to be open and
// driven by hand. Invoked as:
//
//   Unity.exe -quit -batchmode -nographics \
//     -projectPath "<repo>/UnityClient/Aural Primer" \
//     -executeMethod AuralPrimer.EditorTools.BuildCommand.BuildAndroid \
//     -logFile -
//
// Exits non-zero on failure so a caller can tell without parsing the log.

using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public static class BuildCommand
    {
        const string OutputPath = "../Builds/AuralPrimer.apk";

        public static void BuildAndroid()
        {
            var scenes = EditorBuildSettings.scenes
                .Where(s => s.enabled)
                .Select(s => s.path)
                .ToArray();

            if (scenes.Length == 0)
            {
                Debug.LogError("[build] no enabled scenes in Build Settings");
                EditorApplication.Exit(2);
                return;
            }

            Debug.Log($"[build] {scenes.Length} scene(s): {string.Join(", ", scenes)}");

            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = OutputPath,
                target = BuildTarget.Android,
                targetGroup = BuildTargetGroup.Android,
                options = BuildOptions.None,
            });

            var summary = report.summary;
            Debug.Log($"[build] result={summary.result} errors={summary.totalErrors} "
                    + $"time={summary.totalTime} output={summary.outputPath}");

            EditorApplication.Exit(summary.result == BuildResult.Succeeded ? 0 : 1);
        }
    }
}
