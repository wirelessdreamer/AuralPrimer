// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Injects the permissions and features this app needs into the generated
// Android manifest.
//
// Done as a post-processor rather than by dropping an AndroidManifest.xml into
// Assets/Plugins/Android, because that file REPLACES Unity's generated manifest
// wholesale — you inherit responsibility for every Quest entry Unity and the XR
// packages would otherwise have written, and silently lose any they add later.
// Editing the generated file leaves all of that intact.
//
// CHANGE_WIFI_MULTICAST_STATE is the one that matters most here: without it the
// multicast lock cannot be acquired, Android quietly discards multicast, and
// host discovery fails in a way that is indistinguishable from the desktop app
// not running. That is a miserable thing to debug from inside a headset.

using System.IO;
using System.Xml;
using UnityEditor.Android;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public sealed class AndroidManifestPostProcessor : IPostGenerateGradleAndroidProject
    {
        // Runs late, so anything the XR packages add is already present and we
        // only ever append what is missing.
        public int callbackOrder => 100;

        const string AndroidNs = "http://schemas.android.com/apk/res/android";

        static readonly string[] Permissions =
        {
            // Sockets. Unity only adds this automatically when it can tell the
            // build needs it, and raw System.Net.Sockets does not always trip
            // that detection.
            "android.permission.INTERNET",
            // Required by WifiManager.createMulticastLock. Without it discovery
            // silently receives nothing.
            "android.permission.CHANGE_WIFI_MULTICAST_STATE",
            // Reading Wi-Fi state to find the local address during discovery.
            "android.permission.ACCESS_WIFI_STATE",
            "android.permission.ACCESS_NETWORK_STATE",
            // Hand tracking on Horizon OS.
            "com.oculus.permission.HAND_TRACKING",
            // Voice search records a few seconds of speech and sends it to the
            // host to transcribe. Android gates this one behind a runtime
            // prompt as well as the manifest, so Microphone.Start returns null
            // until the user has actually granted it -- see the wizard, which
            // reports that rather than appearing to record nothing.
            "android.permission.RECORD_AUDIO",
        };

        public void OnPostGenerateGradleAndroidProject(string path)
        {
            var manifestPath = Path.Combine(path, "src", "main", "AndroidManifest.xml");
            if (!File.Exists(manifestPath))
            {
                Debug.LogWarning($"[AuralPrimer] no manifest at {manifestPath}; skipping injection.");
                return;
            }

            var doc = new XmlDocument();
            doc.Load(manifestPath);

            var manifest = doc.DocumentElement;
            if (manifest == null)
            {
                Debug.LogError("[AuralPrimer] manifest has no root element.");
                return;
            }

            var added = 0;
            foreach (var permission in Permissions)
            {
                if (HasChildWithName(manifest, "uses-permission", permission)) continue;
                var node = doc.CreateElement("uses-permission");
                node.SetAttribute("name", AndroidNs, permission);
                manifest.AppendChild(node);
                added++;
            }

            // Hand tracking declared optional: the app is perfectly usable with
            // controllers for the menu, and marking it required would exclude
            // devices needlessly.
            if (!HasChildWithName(manifest, "uses-feature", "oculus.software.handtracking"))
            {
                var feature = doc.CreateElement("uses-feature");
                feature.SetAttribute("name", AndroidNs, "oculus.software.handtracking");
                feature.SetAttribute("required", AndroidNs, "false");
                manifest.AppendChild(feature);
                added++;
            }

            if (added > 0)
            {
                doc.Save(manifestPath);
            }
            Debug.Log($"[AuralPrimer] Android manifest: {added} entr{(added == 1 ? "y" : "ies")} added.");
        }

        static bool HasChildWithName(XmlElement parent, string tag, string name)
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
