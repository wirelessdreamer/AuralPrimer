// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Ask for the spatial-data permission that passthrough and anchoring need.
//
// Declaring a permission in the manifest is not the same as holding it. Horizon
// OS gates USE_SCENE behind a runtime prompt, and until the user answers it the
// anchor service reports itself offline: KeyboardAnchor's TryAddAnchorAsync
// fails, calibration cannot be saved, and the wizard asks for the same pinch
// again forever. Nothing in the app said why, because from the inside a denied
// permission and a runtime that has not localised yet look identical.
//
// Asked at launch rather than at the point of use, which is the opposite of how
// the microphone is handled and deliberate. The microphone belongs to one
// optional feature, so asking there gives the prompt a context the user can
// judge. Spatial data is the whole app -- there is no screen to reach, and no
// useful state to be in, without it -- so the honest moment to ask is before
// the first frame.

using UnityEngine;
using UnityEngine.Android;

namespace AuralPrimer.Calibration
{
    /// <summary>
    /// Requests the Horizon OS spatial-data permissions during startup.
    /// </summary>
    public static class SpatialPermissions
    {
        // Both namespaces are requested because the two coexist during Horizon
        // OS's migration: older builds are gated on the com.oculus name, newer
        // ones on horizonos, and which is enforced depends on the OS the app
        // happens to be running on. Asking for one and not the other leaves the
        // app denied on half the fleet for no visible reason.
        static readonly string[] Spatial =
        {
            "com.oculus.permission.USE_SCENE",
            "horizonos.permission.USE_SCENE",
        };

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        static void Request()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            var missing = new System.Collections.Generic.List<string>();
            foreach (var permission in Spatial)
            {
                if (!Permission.HasUserAuthorizedPermission(permission)) missing.Add(permission);
            }

            if (missing.Count == 0)
            {
                Debug.Log("[permissions] spatial data already granted");
                return;
            }

            // Answered asynchronously, and the AR session starts regardless. A
            // denial is therefore not fatal here -- it is reported so the log
            // says which permission is missing, instead of the failure showing
            // up much later as an anchor that will not save.
            var callbacks = new PermissionCallbacks();
            callbacks.PermissionGranted += name =>
                Debug.Log($"[permissions] granted {name}");
            callbacks.PermissionDenied += name =>
            {
                // A denial the system will not prompt for again looks identical
                // to an ordinary one from here, except that the rationale flag
                // is cleared -- so the message can say whether pressing on will
                // ever help, or whether Settings is the only way back.
                var again = Permission.ShouldShowRequestPermissionRationale(name);
                Debug.LogError($"[permissions] DENIED {name} -- passthrough and keyboard "
                             + "anchoring will not work until this is allowed"
                             + (again ? "." : "; the system will not ask again, so allow it in "
                                            + "Settings > Apps > Aural Primer > Permissions."));
            };

            Debug.Log($"[permissions] requesting {string.Join(", ", missing)}");
            Permission.RequestUserPermissions(missing.ToArray(), callbacks);
#endif
        }
    }
}
