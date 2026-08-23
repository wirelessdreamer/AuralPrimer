// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Headless project configuration, so the parts of setup that are easy to get
// subtly wrong by hand are done the same way every time:
//
//   Unity.exe -quit -batchmode -nographics \
//     -projectPath "<repo>/UnityClient/Aural Primer" \
//     -executeMethod AuralPrimer.EditorTools.SetupCommand.Configure \
//     -logFile -
//
// Scene wiring in particular: a missing serialized reference does not fail, it
// produces a build where one feature silently does nothing, which costs a
// headset session to discover.

using System.Linq;
using AuralPrimer.Calibration;
using AuralPrimer.Link;
using AuralPrimer.Recording;
using AuralPrimer.UI;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditor.XR.OpenXR.Features;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public static class SetupCommand
    {
        const string ScenePath = "Assets/AuralPrimer/Scenes/AuralPrimerMR.unity";

        /// <summary>
        /// The Meta feature that lets OVRPlugin run under the OpenXR loader.
        /// </summary>
        /// <remarks>
        /// Referenced by id rather than by type so this script does not fail to
        /// compile when the Meta package is absent. Without this feature enabled
        /// OVRPlugin never initialises, and every body/face/eye probe reports
        /// "unavailable" on hardware that supports all three.
        /// </remarks>
        const string MetaXRFeatureId = "com.meta.openxr.feature.metaxr";

        public static void Configure()
        {
            var ok = EnableMetaXRFeature() & EnableTracking() & WireScene();
            EditorApplication.Exit(ok ? 0 : 1);
        }

        /// <summary>Configure, then build — one headless invocation.</summary>
        public static void ConfigureAndBuild()
        {
            if (!(EnableMetaXRFeature() & EnableTracking() & WireScene()))
            {
                EditorApplication.Exit(1);
                return;
            }
            BuildCommand.BuildAndroid();
        }

        static bool EnableMetaXRFeature()
        {
            var feature = FeatureHelpers.GetFeatureWithIdForBuildTarget(
                BuildTargetGroup.Android, MetaXRFeatureId);

            if (feature == null)
            {
                Debug.LogError($"[setup] OpenXR feature {MetaXRFeatureId} not found. "
                             + "Is com.meta.xr.sdk.core installed?");
                return false;
            }

            if (feature.enabled)
            {
                Debug.Log($"[setup] {MetaXRFeatureId} already enabled");
                return true;
            }

            feature.enabled = true;
            EditorUtility.SetDirty(feature);
            AssetDatabase.SaveAssets();
            Debug.Log($"[setup] enabled {MetaXRFeatureId} for Android");
            return true;
        }

        /// <summary>
        /// Declare what this app actually uses in the Meta project config.
        /// </summary>
        /// <remarks>
        /// These flags are what put the Quest tracking permissions in the
        /// manifest. Adding the uses-permission entries directly does not work:
        /// OVRManifestPreprocessor regenerates the manifest from this config
        /// during the build, so anything written by hand is discarded — silently,
        /// leaving an APK whose every tracking probe reports "unavailable".
        ///
        /// Supported rather than Required: Required means the device must have
        /// the capability, which would make the build refuse to run on a Quest 3.
        /// The recorder already handles a headset that answers "no face" by
        /// leaving the channel out of the file.
        /// </remarks>
        static bool EnableTracking()
        {
            var config = OVRProjectConfig.CachedProjectConfig;
            if (config == null)
            {
                Debug.LogError("[setup] no OVRProjectConfig; is com.meta.xr.sdk.core installed?");
                return false;
            }

            // Hands first, and not optional. Committing this config at all makes
            // OVRManifestPreprocessor authoritative over the manifest, and every
            // field it does not set keeps a default — handTrackingSupport
            // defaults to ControllersOnly, which drops the HAND_TRACKING
            // permission and makes the headset demand controllers on launch for
            // an app that is driven entirely by pinches.
            config.handTrackingSupport = OVRProjectConfig.HandTrackingSupport.ControllersAndHands;

            // Likewise anchors and passthrough: the calibration is stored on a
            // persistent spatial anchor, and the whole point is seeing the real
            // keyboard through the headset. Unity's own settings currently supply
            // both permissions, so these are belt and braces — but a config that
            // declares "no anchors, no passthrough" for an app whose two core
            // features are anchors and passthrough is a trap for the next build.
            config.anchorSupport = OVRProjectConfig.AnchorSupport.Enabled;
            config.insightPassthroughSupport = OVRProjectConfig.FeatureSupport.Supported;

            config.bodyTrackingSupport = OVRProjectConfig.FeatureSupport.Supported;
            config.faceTrackingSupport = OVRProjectConfig.FeatureSupport.Supported;
            config.eyeTrackingSupport = OVRProjectConfig.FeatureSupport.Supported;
            OVRProjectConfig.CommitProjectConfig(config);

            Debug.Log("[setup] OVRProjectConfig: hands=ControllersAndHands, anchors=Enabled, "
                    + "passthrough=Supported, body/face/eye=Supported");
            return true;
        }

        static bool WireScene()
        {
            var scene = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            if (!scene.IsValid())
            {
                Debug.LogError($"[setup] could not open {ScenePath}");
                return false;
            }

            var wizard = Object.FindFirstObjectByType<SetupWizard>();
            var link = Object.FindFirstObjectByType<MrLinkBehaviour>();
            if (wizard == null || link == null)
            {
                Debug.LogError($"[setup] scene is missing SetupWizard={wizard != null} "
                             + $"MrLinkBehaviour={link != null}");
                return false;
            }

            // Live on the same object as the link it reads notes from, so the
            // recorder cannot end up in a scene without one.
            var capture = Object.FindFirstObjectByType<PerformanceCapture>();
            if (capture == null)
            {
                capture = link.gameObject.AddComponent<PerformanceCapture>();
                Debug.Log($"[setup] added PerformanceCapture to {link.gameObject.name}");
            }

            // Playback draws into the same room the capture came from, so it
            // rides alongside the recorder rather than owning its own object.
            var player = Object.FindFirstObjectByType<PerformancePlayback>();
            if (player == null)
            {
                player = link.gameObject.AddComponent<PerformancePlayback>();
                Debug.Log($"[setup] added PerformancePlayback to {link.gameObject.name}");
            }

            var changed = false;
            changed |= AssignIfEmpty(capture, "link", link);
            changed |= AssignIfEmpty(wizard, "capture", capture);
            changed |= AssignIfEmpty(wizard, "playback", player);

            // Bound as project assets so the build keeps their shader variants.
            changed |= AssignMaterial(player, "handMaterial", "PlaybackHand");
            changed |= AssignMaterial(player, "bodyMaterial", "PlaybackBody");
            changed |= AssignMaterial(player, "headMaterial", "PlaybackHead");
            changed |= BuildPokeRigs();

            // The panel builds its own hierarchy at runtime, so the faces have to
            // be handed to it as assets — a TMP font cannot be loaded by name at
            // runtime unless it lives under Resources, and an unassigned one
            // silently falls back to Liberation Sans.
            var panel = Object.FindFirstObjectByType<AuralPrimer.UI.WizardPanel>();
            if (panel != null)
            {
                changed |= AssignAsset<TMPro.TMP_FontAsset>(panel, "displayFont",
                    "Assets/AuralPrimer/Fonts/ChakraPetch-Bold SDF.asset");
                changed |= AssignAsset<TMPro.TMP_FontAsset>(panel, "bodyFont",
                    "Assets/AuralPrimer/Fonts/ChakraPetch-SemiBold SDF.asset");
            }
            else
            {
                Debug.LogWarning("[setup] no WizardPanel in the scene; fonts not assigned");
            }

            if (changed)
            {
                EditorSceneManager.MarkSceneDirty(scene);
                EditorSceneManager.SaveScene(scene);
                Debug.Log("[setup] scene saved");
            }
            else
            {
                Debug.Log("[setup] scene already wired");
            }

            Report(wizard);
            return true;
        }

        /// <summary>
        /// Give each hand a fingertip poke interactor, if it has not got one.
        /// </summary>
        /// <remarks>
        /// Touch is the primary way to work the menu and the ray is the fallback,
        /// so the poke rig is not optional set-dressing — without it the only way
        /// to press a button is to aim a laser at it, which is the interaction the
        /// ray exists to back up rather than to be.
        ///
        /// Built as a sibling of each HandRayDriver: that component already marks
        /// which GameObject belongs to which hand, so nothing here has to guess
        /// or depend on names in the scene.
        /// </remarks>
        static bool BuildPokeRigs()
        {
            var rays = Object.FindObjectsByType<HandRayDriver>(FindObjectsSortMode.None);
            if (rays.Length == 0)
            {
                Debug.LogWarning("[setup] no HandRayDriver in the scene; no poke rigs built");
                return false;
            }

            var built = 0;
            foreach (var ray in rays)
            {
                var isLeft = new SerializedObject(ray).FindProperty("leftHand").boolValue;
                var name = isLeft ? "Left Poke" : "Right Poke";

                var parent = ray.transform.parent;
                if (parent != null && parent.Find(name) != null) continue;

                var go = new GameObject(name);
                go.transform.SetParent(parent, false);

                var poke = go.AddComponent<UnityEngine.XR.Interaction.Toolkit.Interactors.XRPokeInteractor>();
                // UGUI is what the menu is made of, and the canvas already carries
                // a TrackedDeviceGraphicRaycaster for the ray to hit.
                poke.enableUIInteraction = true;
                // No XRPokeFilter on those buttons, and requiring one would mean
                // the fingertip silently passes through every control.
                poke.requirePokeFilter = false;

                var driver = go.AddComponent<HandPokeDriver>();
                driver.LeftHand = isLeft;

                Debug.Log($"[setup] built {name} on {(parent != null ? parent.name : "<root>")}");
                built++;
            }

            if (built == 0) Debug.Log("[setup] poke rigs already present");
            return built > 0;
        }

        /// <summary>
        /// Fill a serialized reference, leaving any existing one alone.
        /// </summary>
        /// <remarks>
        /// These fields are private [SerializeField], so they are reachable only
        /// through SerializedObject. Overwriting a reference someone set on
        /// purpose would make this script destructive to run twice.
        /// </remarks>
        static bool AssignIfEmpty(Object target, string field, Object value)
        {
            var serialized = new SerializedObject(target);
            var property = serialized.FindProperty(field);
            if (property == null)
            {
                Debug.LogError($"[setup] {target.GetType().Name} has no serialized field '{field}'");
                return false;
            }

            if (property.objectReferenceValue != null) return false;

            property.objectReferenceValue = value;
            serialized.ApplyModifiedPropertiesWithoutUndo();
            Debug.Log($"[setup] {target.GetType().Name}.{field} = {value.name}");
            return true;
        }

        static bool AssignMaterial(Object target, string field, string materialName) =>
            AssignAsset<Material>(target, field, $"Assets/AuralPrimer/Materials/{materialName}.mat");

        static bool AssignAsset<T>(Object target, string field, string path) where T : Object
        {
            var asset = AssetDatabase.LoadAssetAtPath<T>(path);
            if (asset == null)
            {
                Debug.LogError($"[setup] missing asset {path}");
                return false;
            }
            return AssignIfEmpty(target, field, asset);
        }

        /// <summary>Print what the wizard ended up holding, so a silent miss is visible.</summary>
        static void Report(SetupWizard wizard)
        {
            var serialized = new SerializedObject(wizard);
            var missing = new[] { "link", "panel", "hands", "overlay", "highway",
                                  "keyboardAnchor", "capture", "playback" }
                .Where(name =>
                {
                    var property = serialized.FindProperty(name);
                    return property == null || property.objectReferenceValue == null;
                })
                .ToArray();

            if (missing.Length == 0) Debug.Log("[setup] every SetupWizard reference is assigned");
            else Debug.LogWarning($"[setup] SetupWizard has unassigned references: {string.Join(", ", missing)}");
        }
    }
}
