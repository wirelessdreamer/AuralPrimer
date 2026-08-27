// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Fail the build if the Android manifest is not a mixed-reality manifest.
//
// The Meta SDK injects the declarations that make this an MR app rather than a
// VR one: the Horizon OS SDK element, the passthrough feature, and the anchor
// permission. When that injection does not happen the build still succeeds, the
// APK still installs, and the failure only appears in the headset -- as a black
// void instead of passthrough, and a calibration step that can never complete
// because the anchor service refuses to connect. Nothing in the build log says
// anything is wrong.
//
// That happened, and cost an afternoon to trace back from the symptom. The
// manifest is cheap to check and the check is exact, so it runs on every
// Android build: an APK missing these is not shippable, and saying so here is
// far cheaper than discovering it on a device.
//
// Ordered after the Meta injector (99999), which is in turn ordered after the
// Oculus XR Plugin's (10000) -- this has to observe the finished manifest.

using System.Collections.Generic;
using System.IO;
using System.Xml;
using UnityEditor.Android;
using UnityEditor.Build;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public sealed class AndroidManifestVerifier : IPostGenerateGradleAndroidProject
    {
        public int callbackOrder => 100000;

        const string AndroidNs = "http://schemas.android.com/apk/res/android";
        const string HorizonNs = "http://schemas.horizonos/sdk";

        public void OnPostGenerateGradleAndroidProject(string path)
        {
            var manifestPath = Path.Combine(path, "src", "main", "AndroidManifest.xml");
            if (!File.Exists(manifestPath)) return;

            var doc = new XmlDocument();
            doc.Load(manifestPath);
            var manifest = doc.DocumentElement;
            if (manifest == null) return;

            var missing = new List<string>();

            // Without this the OS treats the app as a legacy VR app: the OpenXR
            // runtime then offers only the OPAQUE environment blend mode, so
            // passthrough is not merely off, it is unavailable to ask for.
            if (manifest.GetElementsByTagName("uses-horizonos-sdk", HorizonNs).Count == 0)
            {
                missing.Add("<horizonos:uses-horizonos-sdk> (app is not declared as Horizon OS)");
            }

            if (!HasNamed(manifest, "uses-feature", "com.oculus.feature.PASSTHROUGH"))
            {
                missing.Add("uses-feature com.oculus.feature.PASSTHROUGH (no passthrough)");
            }

            // Calibration is stored on a spatial anchor; without this the anchor
            // service stays offline and the wizard loops on the same step.
            if (!HasNamed(manifest, "uses-permission", "com.oculus.permission.USE_ANCHOR_API"))
            {
                missing.Add("uses-permission com.oculus.permission.USE_ANCHOR_API (no anchoring)");
            }

            if (missing.Count == 0)
            {
                Debug.Log("[AuralPrimer] Android manifest: mixed-reality declarations present.");
                return;
            }

            throw new BuildFailedException(
                "[AuralPrimer] the Android manifest is missing declarations this app needs:\n  - "
                + string.Join("\n  - ", missing)
                + $"\n\nManifest: {manifestPath}\n"
                + "These are injected by the Meta XR SDK's build hook. Its absence usually means "
                + "an editor script failed to compile, so the hook never registered. Fix the "
                + "compile error and rebuild -- shipping this APK gives a headset with no "
                + "passthrough and a calibration step that cannot be completed.");
        }

        static bool HasNamed(XmlElement parent, string tag, string name)
        {
            foreach (XmlNode child in parent.ChildNodes)
            {
                if (child is not XmlElement element || element.Name != tag) continue;
                if (element.GetAttribute("name", AndroidNs) == name) return true;
            }
            return false;
        }
    }
}
