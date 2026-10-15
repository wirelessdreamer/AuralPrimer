// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Face and eye channels, which come from Meta's Core SDK rather than from
// OpenXR's cross-vendor surface.
//
// No body channel: Meta's body tracking cannot run alongside simultaneous
// hands and controllers, which the guitar needs -- the controller on its
// mount is set down, and without that mode Horizon OS stops tracking it.
//
// Isolated in its own file behind META_XR_CORE so the client still compiles and
// runs with the Meta package absent — the asmdef defines that symbol only when
// com.meta.xr.sdk.core is installed. Without it the recorder simply declares
// fewer channels, which the header already describes, so old readers keep
// working and nothing downstream needs a special case.
//
// Read through OVRPlugin rather than the OVRBody / OVRFaceExpressions /
// OVREyeGaze components: those require objects wired into the scene, and a
// recorder that silently captures nothing because a component is missing is
// worse than one that reports the channel as unavailable.

using System.Collections.Generic;
using System.IO;
using UnityEngine;

namespace AuralPrimer.Recording
{
    public sealed partial class PerformanceCapture
    {
#if META_XR_CORE

        /// <summary>Face weights in the v2 expression set.</summary>
        const int FaceWeightCount = (int)OVRPlugin.FaceExpression2.Max;

        /// <summary>Left and right.</summary>
        const int EyeCount = (int)OVRPlugin.Eye.Count;

        bool _faceAvailable;
        bool _eyesAvailable;

        OVRPlugin.FaceState _faceState;
        OVRPlugin.EyeGazesState _eyeState;

        /// <summary>
        /// Probe each capability once, and declare only what actually answered.
        /// </summary>
        /// <remarks>
        /// Probed rather than inferred from the headset model: face and eye
        /// tracking are also a user permission and a system setting, so a Quest
        /// Pro with face tracking switched off must record as "no face channel"
        /// rather than as a Quest Pro.
        /// </remarks>
        IEnumerable<string> MetaChannels()
        {
            var channels = new List<string>();

            _faceAvailable = OVRPlugin.GetFaceState2(OVRPlugin.Step.Render, -1, ref _faceState)
                          && _faceState.ExpressionWeights != null;
            if (_faceAvailable) channels.Add(Channel("face", "weights", FaceWeightCount));

            _eyesAvailable = OVRPlugin.GetEyeGazesState(OVRPlugin.Step.Render, -1, ref _eyeState)
                          && _eyeState.EyeGazes != null;
            if (_eyesAvailable) channels.Add(Channel("eyes", "pose", EyeCount));

            Debug.Log($"[capture] meta channels: face={_faceAvailable}, eyes={_eyesAvailable}");
            return channels;
        }

        void WriteMetaChannels(BinaryWriter frame)
        {
            if (_faceAvailable)
            {
                var got = OVRPlugin.GetFaceState2(OVRPlugin.Step.Render, -1, ref _faceState)
                       && _faceState.ExpressionWeights != null;

                for (var i = 0; i < FaceWeightCount; i++)
                {
                    frame.Write(got && i < _faceState.ExpressionWeights.Length
                        ? _faceState.ExpressionWeights[i]
                        : 0f);
                }
            }

            if (_eyesAvailable)
            {
                var got = OVRPlugin.GetEyeGazesState(OVRPlugin.Step.Render, -1, ref _eyeState)
                       && _eyeState.EyeGazes != null;

                for (var i = 0; i < EyeCount; i++)
                {
                    var pose = default(Pose);
                    if (got && i < _eyeState.EyeGazes.Length && _eyeState.EyeGazes[i].IsValid)
                    {
                        pose = ToUnity(_eyeState.EyeGazes[i].Pose);
                    }
                    WritePose(frame, pose);
                }
            }
        }

        /// <summary>
        /// Meta's tracking space is right-handed; Unity's is left-handed.
        /// </summary>
        /// <remarks>
        /// Recording the raw values would store a mirrored skeleton that looks
        /// almost right — which is the worst kind of wrong, because it survives a
        /// casual look at the data and only shows up as a left-handed player once
        /// something is rendered from it.
        /// </remarks>
        static Pose ToUnity(OVRPlugin.Posef pose)
        {
            var converted = pose.ToOVRPose();
            return new Pose(converted.position, converted.orientation);
        }

#else

        // No Meta package: the recording simply has no face or eye
        // channels, and its header says so.
        IEnumerable<string> MetaChannels() => System.Array.Empty<string>();

        void WriteMetaChannels(BinaryWriter frame) { }

#endif
    }
}
