// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// An invisible mesh that still writes depth — a hole punched in the overlay in
// the shape of the player's hand.
//
// Passthrough on Quest is composited by the runtime, not drawn by the app, so
// there is no camera image in the frame for scene depth to test our geometry
// against. Environment-depth occlusion therefore cannot hide the drawn keys.
// What can is this: render the tracked hand mesh before everything else with
// colour writes off, so it contributes nothing visible but leaves its shape in
// the depth buffer. Anything drawn afterwards fails the depth test there, the
// app draws nothing over those pixels, and the compositor shows the real hand.
//
// Queue is Geometry-1 so the mask is laid down before the geometry it has to
// mask; ColorMask 0 is what keeps the mask itself from ever being seen.

Shader "AuralPrimer/HandOcclusion"
{
    SubShader
    {
        Tags
        {
            "RenderType" = "Opaque"
            "Queue" = "Geometry-1"
            "RenderPipeline" = "UniversalPipeline"
        }

        Pass
        {
            Name "HandDepthMask"

            ColorMask 0
            ZWrite On
            ZTest LEqual
            Cull Back

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
                UNITY_VERTEX_INPUT_INSTANCE_ID
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                UNITY_VERTEX_OUTPUT_STEREO
            };

            Varyings vert(Attributes input)
            {
                Varyings output = (Varyings)0;
                UNITY_SETUP_INSTANCE_ID(input);
                UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(output);
                output.positionCS = TransformObjectToHClip(input.positionOS.xyz);
                return output;
            }

            half4 frag(Varyings input) : SV_Target
            {
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(input);
                // Never seen: ColorMask 0 discards this. The pass exists purely
                // for the depth it leaves behind.
                return half4(0, 0, 0, 0);
            }
            ENDHLSL
        }
    }

    Fallback Off
}
