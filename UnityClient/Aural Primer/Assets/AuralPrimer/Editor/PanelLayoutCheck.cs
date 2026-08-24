// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Dumps the wizard panel's actual built hierarchy.
//
//   Unity.exe -quit -batchmode -nographics \
//     -projectPath "<repo>/UnityClient/Aural Primer" \
//     -executeMethod AuralPrimer.EditorTools.PanelLayoutCheck.Run \
//     -logFile -
//
// Written because "it is in the code and it compiles" was offered twice as
// evidence that a control was on screen. It is not evidence of anything: the
// element can exist, compile, and still be behind an opaque sibling, outside
// its parent's rect, or zero-sized. This prints where every piece actually
// landed so that question is answered by measurement.

using AuralPrimer.UI;
using UnityEditor;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public static class PanelLayoutCheck
    {
        public static void Run()
        {
            var host = new GameObject("Panel Check");
            var panel = host.AddComponent<WizardPanel>();

            // Awake is where the hierarchy is built; AddComponent does not fire
            // lifecycle messages outside play mode.
            panel.SendMessage("Awake");

            var canvas = host.transform.Find("Canvas");
            if (canvas == null)
            {
                Debug.LogError("[panel] no Canvas was built");
                EditorApplication.Exit(1);
                return;
            }

            var canvasRect = (RectTransform)canvas;
            Debug.Log($"[panel] canvas {canvasRect.sizeDelta.x}x{canvasRect.sizeDelta.y} "
                    + $"scale {canvasRect.localScale.x:F5}");
            Debug.Log($"[panel] draw order is hierarchy order — later children paint over earlier ones");

            for (var i = 0; i < canvas.childCount; i++)
            {
                var child = canvas.GetChild(i);
                var rect = child as RectTransform;
                if (rect == null) continue;

                var corners = new Vector3[4];
                rect.GetLocalCorners(corners);
                // Corners are relative to the rect; offset into canvas space.
                var centre = (Vector3)rect.anchoredPosition;
                var anchoredTop = centre.y + corners[1].y;
                var anchoredBottom = centre.y + corners[0].y;

                var image = child.GetComponent<UnityEngine.UI.Image>();
                var opaque = image != null && image.color.a > 0.98f;

                Debug.Log($"[panel] {i,2}. {child.name,-14} "
                        + $"active={child.gameObject.activeSelf,-5} "
                        + $"anchor=({rect.anchorMin.x:F1},{rect.anchorMin.y:F1}) "
                        + $"pos=({rect.anchoredPosition.x:F0},{rect.anchoredPosition.y:F0}) "
                        + $"size=({rect.rect.width:F0}x{rect.rect.height:F0}) "
                        + $"img={(image != null ? $"a{image.color.a:F2}{(opaque ? " OPAQUE" : "")}" : "-")}");
            }

            // The specific question: is the tilt bar there, and is anything
            // painted after it that would cover it?
            var tilt = canvas.Find("Tilt Handle");
            if (tilt == null)
            {
                Debug.LogError("[panel] FAIL: no 'Tilt Handle' child exists");
            }
            else
            {
                var index = tilt.GetSiblingIndex();
                var covered = false;
                for (var i = index + 1; i < canvas.childCount; i++)
                {
                    var later = canvas.GetChild(i);
                    var image = later.GetComponent<UnityEngine.UI.Image>();
                    var rect = later as RectTransform;
                    // A later sibling that stretches the whole canvas and is not
                    // transparent paints straight over the bar.
                    if (image != null && image.color.a > 0.5f && rect != null
                        && Mathf.Approximately(rect.anchorMin.x, 0f)
                        && Mathf.Approximately(rect.anchorMax.x, 1f)
                        && Mathf.Approximately(rect.anchorMin.y, 0f)
                        && Mathf.Approximately(rect.anchorMax.y, 1f))
                    {
                        Debug.LogError($"[panel] FAIL: '{later.name}' is drawn after the tilt bar and covers it");
                        covered = true;
                    }
                }

                var rectT = (RectTransform)tilt;
                var inside = rectT.rect.height > 0.5f && rectT.rect.width > 0.5f;
                Debug.Log($"[panel] tilt bar: siblingIndex={index}/{canvas.childCount - 1} "
                        + $"sized={inside} covered={covered}");

                if (inside && !covered) Debug.Log("[panel] OK: tilt bar exists, is sized, and nothing paints over it");
            }

            var colliders = host.GetComponents<BoxCollider>();
            Debug.Log($"[panel] grab colliders on the root: {colliders.Length} (want 2 — bottom bar and top bar)");
            foreach (var collider in colliders)
            {
                Debug.Log($"[panel]   centre y={collider.center.y:F4} size={collider.size}");
            }

            Object.DestroyImmediate(host);
            EditorApplication.Exit(0);
        }
    }
}
