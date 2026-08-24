// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Bakes the Chakra Petch TTFs into TextMeshPro font assets.
//
// TMP cannot render a .ttf directly — it needs an SDF atlas baked from one, and
// that bake is normally a manual trip through a GUI window. Doing it in script
// means the font a build ships with is reproducible from the repo rather than
// from someone having remembered to press a button.
//
//   Unity.exe -quit -batchmode -nographics \
//     -projectPath "<repo>/UnityClient/Aural Primer" \
//     -executeMethod AuralPrimer.EditorTools.FontCommand.BakeFonts \
//     -logFile -

using System.IO;
using TMPro;
using UnityEditor;
using UnityEngine;

namespace AuralPrimer.EditorTools
{
    public static class FontCommand
    {
        const string Folder = "Assets/AuralPrimer/Fonts";

        // Dynamic atlases grow as glyphs are needed, so the starting size only
        // has to hold the menu's own text rather than every glyph in the face.
        const int AtlasWidth = 512;
        const int AtlasHeight = 512;
        const int SamplingSize = 72;
        const int Padding = 8;

        public static void BakeFonts()
        {
            var made = 0;
            foreach (var weight in new[] { "Bold", "SemiBold" })
            {
                if (Bake($"ChakraPetch-{weight}")) made++;
            }

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            Debug.Log($"[fonts] baked {made} TMP font asset(s)");
            EditorApplication.Exit(made == 2 ? 0 : 1);
        }

        static bool Bake(string name)
        {
            var ttf = $"{Folder}/{name}.ttf";
            var output = $"{Folder}/{name} SDF.asset";

            if (File.Exists(output))
            {
                Debug.Log($"[fonts] {name} SDF already exists");
                return true;
            }

            var font = AssetDatabase.LoadAssetAtPath<Font>(ttf);
            if (font == null)
            {
                Debug.LogError($"[fonts] cannot load {ttf}");
                return false;
            }

            var asset = TMP_FontAsset.CreateFontAsset(
                font, SamplingSize, Padding,
                UnityEngine.TextCore.LowLevel.GlyphRenderMode.SDFAA,
                AtlasWidth, AtlasHeight,
                AtlasPopulationMode.Dynamic);

            if (asset == null)
            {
                Debug.LogError($"[fonts] TMP could not bake {name}");
                return false;
            }

            asset.name = $"{name} SDF";
            AssetDatabase.CreateAsset(asset, output);

            // The atlas texture and material are sub-assets of the font asset;
            // saved loose they would be lost on the next import and the text
            // would render as blank quads.
            if (asset.atlasTextures != null)
            {
                foreach (var texture in asset.atlasTextures)
                {
                    if (texture == null) continue;
                    texture.name = $"{name} Atlas";
                    AssetDatabase.AddObjectToAsset(texture, asset);
                }
            }
            if (asset.material != null)
            {
                asset.material.name = $"{name} Material";
                AssetDatabase.AddObjectToAsset(asset.material, asset);
            }

            Debug.Log($"[fonts] baked {output}");
            return true;
        }
    }
}
