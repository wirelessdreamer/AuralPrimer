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
//
// Pass -versionCode N to pin the Android version code; otherwise it is derived
// (see ResolveVersionCode).

using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public static class BuildCommand
    {
        const string OutputPath = "../Builds/AuralPrimer.apk";

        /// <summary>Fixed origin for the derived version code. Never move it.</summary>
        static readonly DateTime VersionEpoch = new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc);

        /// <summary>
        /// The Android version code for this build.
        ///
        /// Meta rejects any upload whose code is not strictly greater than the
        /// highest already accepted, and it tracks that per app rather than per
        /// release channel -- so a reused number fails *after* the upload, at the
        /// end of a long build, rather than before it.
        ///
        /// It is derived rather than stored. A counter committed to
        /// ProjectSettings.asset reads tidier but is not monotonic in this repo:
        /// a build from a worktree, from a branch that never merged, or after a
        /// rebase all hand back a number that has already been spent. Minutes
        /// elapsed since a fixed epoch cannot regress, needs nothing committed
        /// and so dirties no file, and leaves Android's 2100000000 ceiling about
        /// four thousand years out.
        ///
        /// An explicit value still wins, so re-cutting a specific upload is
        /// reproducible: -versionCode N on the command line, or AURAL_VERSION_CODE
        /// in the environment for a CI runner that would rather not build argv.
        /// </summary>
        static int ResolveVersionCode()
        {
            var args = Environment.GetCommandLineArgs();
            for (var i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == "-versionCode" && int.TryParse(args[i + 1], out var pinned) && pinned > 0)
                {
                    return pinned;
                }
            }

            if (int.TryParse(Environment.GetEnvironmentVariable("AURAL_VERSION_CODE"), out var fromEnv)
                && fromEnv > 0)
            {
                return fromEnv;
            }

            // Clamped, because a machine whose clock predates the epoch would
            // otherwise produce a negative code and fail far less legibly.
            var minutes = (DateTime.UtcNow - VersionEpoch).TotalMinutes;
            return (int)Math.Max(1d, Math.Min(minutes, 2_100_000_000d));
        }

        /// <summary>
        /// Points the build at the release keystore, taken from the environment.
        ///
        /// Setting a keystore through the Editor writes its path, alias and
        /// *both passwords* into ProjectSettings.asset -- a tracked file. That
        /// puts the password of a key that can never be rotated into version
        /// control, where it survives every later removal in the history. So the
        /// values are read at build time and the asset is left alone.
        ///
        /// Unset is a legitimate state, not an error: the build then falls back
        /// to Unity's debug key, which is right for adb install and wrong for
        /// anything else. upload-quest-build.mjs refuses debug-signed APKs, so
        /// the two halves of the pipeline agree about what is shippable.
        /// </summary>
        static bool ApplyReleaseSigning()
        {
            var keystore = Environment.GetEnvironmentVariable("AURAL_KEYSTORE");
            if (string.IsNullOrEmpty(keystore))
            {
                PlayerSettings.Android.useCustomKeystore = false;
                Debug.Log("[build] signing=debug (AURAL_KEYSTORE unset; not uploadable)");
                return false;
            }

            var storePass = Environment.GetEnvironmentVariable("AURAL_KEYSTORE_PASS");
            var alias = Environment.GetEnvironmentVariable("AURAL_KEY_ALIAS");
            var keyPass = Environment.GetEnvironmentVariable("AURAL_KEY_PASS");

            if (!File.Exists(keystore))
            {
                Debug.LogError($"[build] AURAL_KEYSTORE points at nothing: {keystore}");
                return false;
            }
            if (string.IsNullOrEmpty(storePass) || string.IsNullOrEmpty(alias)
                || string.IsNullOrEmpty(keyPass))
            {
                Debug.LogError("[build] AURAL_KEYSTORE is set but AURAL_KEYSTORE_PASS, "
                             + "AURAL_KEY_ALIAS or AURAL_KEY_PASS is missing");
                return false;
            }

            PlayerSettings.Android.useCustomKeystore = true;
            PlayerSettings.Android.keystoreName = keystore;
            PlayerSettings.Android.keystorePass = storePass;
            PlayerSettings.Android.keyaliasName = alias;
            PlayerSettings.Android.keyaliasPass = keyPass;
            Debug.Log($"[build] signing=release keystore={Path.GetFileName(keystore)} alias={alias}");
            return true;
        }

        /// <summary>
        /// Drops the passwords out of PlayerSettings before the Editor can
        /// serialise them. Unity may write ProjectSettings.asset on quit, and
        /// the one thing that must never end up in that file is the half of the
        /// signing material that is actually secret. The path and alias are not.
        /// </summary>
        static void ForgetSigningSecrets()
        {
            PlayerSettings.Android.keystorePass = "";
            PlayerSettings.Android.keyaliasPass = "";
        }

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

            var versionCode = ResolveVersionCode();
            PlayerSettings.Android.bundleVersionCode = versionCode;
            Debug.Log($"[build] versionCode={versionCode} versionName={PlayerSettings.bundleVersion} "
                    + $"utc={DateTime.UtcNow:yyyy-MM-ddTHH:mm}Z");

            var signed = ApplyReleaseSigning();

            BuildReport report;
            try
            {
                report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
                {
                    scenes = scenes,
                    locationPathName = OutputPath,
                    target = BuildTarget.Android,
                    targetGroup = BuildTargetGroup.Android,
                    options = BuildOptions.None,
                });
            }
            finally
            {
                ForgetSigningSecrets();
            }

            var summary = report.summary;
            Debug.Log($"[build] result={summary.result} errors={summary.totalErrors} "
                    + $"time={summary.totalTime} signed={(signed ? "release" : "debug")} "
                    + $"output={summary.outputPath}");

            EditorApplication.Exit(summary.result == BuildResult.Succeeded ? 0 : 1);
        }
    }
}
