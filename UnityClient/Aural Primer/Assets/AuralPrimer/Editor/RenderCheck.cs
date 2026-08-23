// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Renders the interface to PNG files so it can be LOOKED AT.
//
//   Unity.exe -quit -batchmode \
//     -projectPath "<repo>/UnityClient/Aural Primer" \
//     -executeMethod AuralPrimer.EditorTools.RenderCheck.Run \
//     -logFile -
//
// Note the absence of -nographics: this needs a real graphics device, because
// the whole point is to rasterise.
//
// Every check before this one asserted that objects existed, were sized, and
// were not covered — and every one of them passed while the thing was invisible
// in the headset. Existence is not appearance. A control can be built, sized,
// unoccluded, and still be five millimetres of low-alpha grey that nobody will
// ever find. The only honest test of "is it on screen" is a picture.

using System.IO;
using AuralPrimer.Calibration;
using AuralPrimer.UI;
using UnityEditor;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public static class RenderCheck
    {
        const int Width = 1200;
        const int Height = 820;
        static readonly string OutputDir = Path.Combine(Directory.GetCurrentDirectory(), "..", "RenderChecks");

        public static void Run()
        {
            Directory.CreateDirectory(OutputDir);

            var camera = NewCamera();

            // --- the docked menu, as the player meets it ---------------------
            var panelHost = new GameObject("Panel");
            var panel = panelHost.AddComponent<WizardPanel>();

            // Bind the faces BEFORE Awake builds the hierarchy. A fresh
            // AddComponent has empty serialized fields, so without this the
            // picture shows TMP's fallback and quietly misrepresents the device.
            BindAsset(panel, "displayFont", "Assets/AuralPrimer/Fonts/ChakraPetch-Bold SDF.asset");
            BindAsset(panel, "bodyFont", "Assets/AuralPrimer/Fonts/ChakraPetch-SemiBold SDF.asset");

            panel.SendMessage("Awake");

            // Face the camera: a world-space canvas is read from its -Z side, so
            // its forward points the same way the viewer is looking.
            panelHost.transform.SetPositionAndRotation(
                new Vector3(0f, 0f, 0.42f), Quaternion.identity);

            panel.SetTitle("AuralPrimer");
            panel.SetBody("<b>My keyboard</b> — 61 keys, C2 to C7.\n\nDrag the bar below to move "
                        + "this menu. Hold a palm toward your face to bring it back.");
            panel.SetStatus("connected to STUDIO-PC", new Color(0.208f, 0.941f, 1f));
            panel.SetButtons(
                ("Configure", () => { }),
                ("Record", () => { }),
                ("Watch", () => { }));

            Shoot(camera, "menu.png");

            // --- the transport, with a scrub bar -----------------------------
            panel.SetTitle("Watching");
            panel.SetBody("<b>2026-08-22_13-17-35</b>\n\nThe recorded hands play over your "
                        + "keyboard. Drag the bar to scrub.");
            panel.SetScrub(_ => { }, 0.38f);
            panel.SetButtons(("Pause", () => { }), ("-10s", () => { }), ("+10s", () => { }), ("Back", () => { }));
            Shoot(camera, "watching.png");

            Object.DestroyImmediate(panelHost);

            // --- the keyboard overlay, lane and edge handles -----------------
            ShootKeyboard(camera);

            // --- the whole thing, at real scale, from the seat ---------------
            ShootSeatedScene(camera);

            // --- how an onset reads against a hold ---------------------------
            ShootNoteShapes(camera);

            Object.DestroyImmediate(camera.gameObject);

            Debug.Log($"[render] wrote PNGs to {Path.GetFullPath(OutputDir)}");
            EditorApplication.Exit(0);
        }

        static Camera NewCamera()
        {
            var go = new GameObject("Render Camera");
            var camera = go.AddComponent<Camera>();
            camera.clearFlags = CameraClearFlags.SolidColor;
            // Not black: passthrough is a lit room, and a control that only reads
            // against pure black is a control that does not read.
            camera.backgroundColor = new Color(0.10f, 0.10f, 0.12f);
            camera.fieldOfView = 60f;
            camera.nearClipPlane = 0.01f;
            go.transform.SetPositionAndRotation(Vector3.zero, Quaternion.identity);
            return camera;
        }

        static void ShootKeyboard(Camera camera)
        {
            // A level metre-wide bed, half a metre in front, below eye line —
            // roughly what a seated player sees.
            var profile = new CalibrationProfile
            {
                profileName = "RenderCheck",
                lowestPitch = 36,
                highestPitch = 96,
                leftEdge = new Vector3(-0.5f, -0.28f, 0.62f),
                rightEdge = new Vector3(0.5f, -0.28f, 0.62f),
                up = Vector3.up,
            };

            var overlayHost = new GameObject("Overlay");
            var overlay = overlayHost.AddComponent<KeyboardOverlay>();
            overlay.SendMessage("Awake");
            Bind(overlay, "idleWhiteMaterial", "KeyIdleWhite");
            Bind(overlay, "idleBlackMaterial", "KeyIdleBlack");
            Bind(overlay, "litMaterial", "KeyLit");
            overlay.SendMessage("Awake");
            overlay.Apply(profile);

            var handlesHost = new GameObject("Handles");
            var handles = handlesHost.AddComponent<EdgeHandles>();
            handles.Show(profile, overlayHost.transform,
                         Load("PinchLeft"), Load("PinchRight"));

            camera.transform.SetPositionAndRotation(
                new Vector3(0f, 0f, 0f), Quaternion.Euler(14f, 0f, 0f));
            Shoot(camera, "keyboard.png");

            Object.DestroyImmediate(handlesHost);
            Object.DestroyImmediate(overlayHost);
        }

        /// <summary>
        /// The room as the player meets it: eye at the origin, instrument in
        /// front and below, menu docked where DockBesideKeyboard puts it.
        /// </summary>
        /// <remarks>
        /// Rendering the panel alone, filling the frame, is what let a layout
        /// this broken survive review. At real scale and real distance the
        /// questions that matter become answerable — is the menu readable, is it
        /// square to anything, can a bar be found on it at all.
        /// </remarks>
        static void ShootSeatedScene(Camera camera)
        {
            // Seated at the origin, keys 0.5 m ahead and 0.35 m down.
            var bedCentre = new Vector3(0f, -0.35f, 0.50f);
            var profile = new CalibrationProfile
            {
                profileName = "Seated",
                lowestPitch = 36,
                highestPitch = 96,
                leftEdge = bedCentre + Vector3.left * 0.5f,
                rightEdge = bedCentre + Vector3.right * 0.5f,
                up = Vector3.up,
            };

            var overlayHost = new GameObject("Overlay");
            var overlay = overlayHost.AddComponent<KeyboardOverlay>();
            Bind(overlay, "idleWhiteMaterial", "KeyIdleWhite");
            Bind(overlay, "idleBlackMaterial", "KeyIdleBlack");
            Bind(overlay, "litMaterial", "KeyLit");
            overlay.SendMessage("Awake");
            overlay.Apply(profile);

            var panelHost = new GameObject("Docked Menu");
            var panel = panelHost.AddComponent<WizardPanel>();
            BindAsset(panel, "displayFont", "Assets/AuralPrimer/Fonts/ChakraPetch-Bold SDF.asset");
            BindAsset(panel, "bodyFont", "Assets/AuralPrimer/Fonts/ChakraPetch-SemiBold SDF.asset");
            panel.SendMessage("Awake");

            // The same arithmetic DockBesideKeyboard runs, kept in step by hand.
            const float dockMargin = 0.22f;
            const float dockHeight = 0.20f;
            var position = profile.rightEdge
                         + profile.RightAxis * dockMargin
                         + Vector3.up * dockHeight;

            // dockAngledAway: square to the instrument, using its own normal.
            var normal = Vector3.Cross(profile.RightAxis, Vector3.up).normalized;
            normal.y = 0f;
            normal.Normalize();
            var toPlayer = Vector3.zero - position;
            toPlayer.y = 0f;
            if (Vector3.Dot(normal, toPlayer.normalized) < 0f) normal = -normal;

            panel.PlaceAt(position, Quaternion.LookRotation(-normal, Vector3.up));
            panel.SetTitle("AuralPrimer");
            panel.SetBody("<b>My keyboard</b> — 61 keys, C2 to C7.");
            panel.SetStatus("connected to STUDIO-PC", new Color(0.208f, 0.941f, 1f));
            panel.SetButtons(("Configure", () => { }), ("Record", () => { }), ("Watch", () => { }));

            Debug.Log($"[render] seated: menu at {position:F3}, facing {-normal:F3}, "
                    + $"{Vector3.Distance(Vector3.zero, position):F2}m from the eye");

            // Look at the instrument, as a player would.
            camera.transform.SetPositionAndRotation(Vector3.zero, Quaternion.Euler(20f, 12f, 0f));
            Shoot(camera, "seated.png");

            Object.DestroyImmediate(panelHost);
            Object.DestroyImmediate(overlayHost);
        }

        /// <summary>
        /// Onset versus sustain, at the proportions NoteHighway draws them.
        /// </summary>
        /// <remarks>
        /// A look preview, NOT a test of NoteHighway: that needs a live link for
        /// song time and cannot run headless. It answers the only question being
        /// asked of the shape — can a strike be told from a hold at a glance —
        /// and the numbers are kept in step with the real ones by hand.
        /// </remarks>
        static void ShootNoteShapes(Camera camera)
        {
            const float keyWidth = 0.023f;      // one white key
            const float holdFraction = 0.42f;   // NoteHighway.holdWidthFraction
            const float thickness = 0.003f;
            const float headLength = 0.014f;
            const float gap = 0.006f;

            var root = new GameObject("Notes").transform;
            var head = Load("HitLine");
            var body = Load("NoteWhite");

            // left: one long hold. middle: the same note struck three times.
            // right: a short stab. If the middle does not read as three events,
            // the shape has failed.
            void Note(float x, float bottom, float span)
            {
                var length = Mathf.Max(0.004f, span - gap);

                var tail = GameObject.CreatePrimitive(PrimitiveType.Cube);
                tail.transform.SetParent(root, false);
                tail.transform.localPosition = new Vector3(x, bottom + length * 0.5f, 0f);
                tail.transform.localScale = new Vector3(keyWidth * holdFraction, length, thickness);
                if (body != null) tail.GetComponent<Renderer>().sharedMaterial = body;

                var cap = GameObject.CreatePrimitive(PrimitiveType.Cube);
                cap.transform.SetParent(root, false);
                var capLength = Mathf.Min(headLength, length);
                cap.transform.localPosition = new Vector3(x, bottom + capLength * 0.5f, 0f);
                cap.transform.localScale = new Vector3(keyWidth, capLength, thickness * 1.6f);
                if (head != null) cap.GetComponent<Renderer>().sharedMaterial = head;
            }

            Note(-0.05f, 0.00f, 0.16f);                       // one long hold
            Note(0.00f, 0.00f, 0.05f);                        // struck
            Note(0.00f, 0.055f, 0.05f);                       // struck again
            Note(0.00f, 0.110f, 0.05f);                       // and again
            Note(0.05f, 0.00f, 0.02f);                        // a short stab

            root.position = new Vector3(0f, -0.06f, 0.30f);

            camera.transform.SetPositionAndRotation(Vector3.zero, Quaternion.identity);
            Shoot(camera, "notes.png");

            Object.DestroyImmediate(root.gameObject);
        }

        static void Bind(Object target, string field, string materialName)
        {
            var serialized = new SerializedObject(target);
            var property = serialized.FindProperty(field);
            if (property == null) { Debug.LogWarning($"[render] no field {field}"); return; }
            property.objectReferenceValue = Load(materialName);
            serialized.ApplyModifiedPropertiesWithoutUndo();
        }

        static void BindAsset(Object target, string field, string path)
        {
            var serialized = new SerializedObject(target);
            var property = serialized.FindProperty(field);
            if (property == null) { Debug.LogWarning($"[render] no field {field}"); return; }
            property.objectReferenceValue = AssetDatabase.LoadAssetAtPath<Object>(path);
            serialized.ApplyModifiedPropertiesWithoutUndo();
        }

        static Material Load(string name) =>
            AssetDatabase.LoadAssetAtPath<Material>($"Assets/AuralPrimer/Materials/{name}.mat");

        static void Shoot(Camera camera, string fileName)
        {
            // UGUI and TMP lay out on their own schedule; without forcing it the
            // first frame renders empty rects and the picture lies in the other
            // direction.
            Canvas.ForceUpdateCanvases();
            foreach (var text in Object.FindObjectsByType<TMPro.TextMeshProUGUI>(FindObjectsSortMode.None))
            {
                text.ForceMeshUpdate();
            }
            Canvas.ForceUpdateCanvases();

            var target = new RenderTexture(Width, Height, 24, RenderTextureFormat.ARGB32)
            {
                antiAliasing = 4,
            };
            camera.targetTexture = target;
            camera.Render();

            var previous = RenderTexture.active;
            RenderTexture.active = target;
            var image = new Texture2D(Width, Height, TextureFormat.RGB24, false);
            image.ReadPixels(new Rect(0, 0, Width, Height), 0, 0);
            image.Apply();
            RenderTexture.active = previous;

            camera.targetTexture = null;
            var path = Path.Combine(OutputDir, fileName);
            File.WriteAllBytes(path, image.EncodeToPNG());
            Debug.Log($"[render] {fileName} ({new FileInfo(path).Length} bytes)");

            Object.DestroyImmediate(image);
            target.Release();
            Object.DestroyImmediate(target);
        }
    }
}
