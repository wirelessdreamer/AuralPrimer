// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Records a performance: what the player did, not what the headset drew.
//
// The output is pose data rather than video, because the point is a performance
// that can be re-rendered, retargeted onto a character, or lined up against the
// notes that were actually played. Video would be pixels of one particular
// camera angle in one particular room.
//
// Channels are captured only if the hardware provides them, and the header
// records which ones were present. Quest 3 has no inward-facing cameras, so a
// recording made there carries body and hands but no face or eyes; the same
// file format describes both cases rather than pretending a Quest 3 produced
// zeroed face data.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using AuralPrimer.Link;
using UnityEngine;
using UnityEngine.XR.Hands;
using UnityEngine.XR.Management;

namespace AuralPrimer.Recording
{
    [AddComponentMenu("AuralPrimer/Performance Capture")]
    public sealed partial class PerformanceCapture : MonoBehaviour
    {
        /// <summary>Bumped when the frame layout changes meaning.</summary>
        public const int FormatVersion = 1;

        /// <summary>Floats per pose: position xyz, rotation xyzw.</summary>
        const int FloatsPerPose = 7;

        /// <summary>Every joint XRHands reports, including the wrist and palm.</summary>
        const int HandJointCount = (int)XRHandJointID.EndMarker - (int)XRHandJointID.BeginMarker;

        [SerializeField] MrLinkBehaviour link;

        [Tooltip("Where recordings are written. Empty means "
               + "<persistentDataPath>/recordings, which adb can pull from.")]
        [SerializeField] string outputDirectory = "";

        public bool IsRecording => _writer != null;

        /// <summary>Seconds captured so far, or zero when idle.</summary>
        public double ElapsedSeconds => IsRecording ? _elapsed : 0.0;

        /// <summary>Path of the recording in progress, or the last one written.</summary>
        public string LastPath { get; private set; }

        BinaryWriter _writer;
        XRHandSubsystem _hands;
        double _elapsed;
        int _frames;

        // Reused every frame: a recorder that allocates per frame is a recorder
        // that shows up as GC spikes in the thing it is recording.
        readonly List<float> _scratch = new();
        readonly MemoryStream _frameBuffer = new();

        void Awake() => RequestTrackingPermissions();

        void OnDestroy() => Stop();

        /// <summary>
        /// Ask for the tracking permissions up front, not at the first take.
        /// </summary>
        /// <remarks>
        /// Declaring them in the manifest is only half of it: on Quest these are
        /// runtime permissions, and an ungranted one makes the probe answer
        /// "unavailable" — indistinguishable from hardware that cannot do it. A
        /// Quest 3 recording came back with hands and face but no body channel
        /// for exactly this reason.
        ///
        /// Requested in Awake so the dialog is answered long before anyone
        /// presses Record; asking mid-take would interrupt the performance being
        /// captured.
        /// </remarks>
        static void RequestTrackingPermissions()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            foreach (var permission in new[]
                     {
                         "com.oculus.permission.BODY_TRACKING",
                         "com.oculus.permission.FACE_TRACKING",
                         "com.oculus.permission.EYE_TRACKING",
                     })
            {
                if (!UnityEngine.Android.Permission.HasUserAuthorizedPermission(permission))
                {
                    UnityEngine.Android.Permission.RequestUserPermission(permission);
                }
            }
#endif
        }

        /// <summary>
        /// Begin capturing. Does nothing if already recording.
        /// </summary>
        /// <remarks>
        /// Deliberately NOT called Start. Unity treats Start as a lifecycle
        /// message and calls it on the first frame regardless of who else
        /// might, so naming this method Start made the app begin recording the
        /// instant the scene loaded and keep going until it was killed — two
        /// full-session takes nobody asked for, and a file growing at 127 KB/s
        /// behind an idle menu.
        /// </remarks>
        public string Begin()
        {
            if (IsRecording) return LastPath;

            var directory = string.IsNullOrEmpty(outputDirectory)
                ? Path.Combine(Application.persistentDataPath, "recordings")
                : outputDirectory;

            try
            {
                Directory.CreateDirectory(directory);
            }
            catch (Exception e)
            {
                Debug.LogError($"[capture] cannot create {directory}: {e.Message}");
                return null;
            }

            // Sortable, and unique without a counter to collide on.
            var stamp = DateTime.Now.ToString("yyyy-MM-dd_HH-mm-ss", CultureInfo.InvariantCulture);
            var path = Path.Combine(directory, $"{stamp}.auralperf");

            try
            {
                _writer = new BinaryWriter(File.Open(path, FileMode.Create, FileAccess.Write));
            }
            catch (Exception e)
            {
                Debug.LogError($"[capture] cannot open {path}: {e.Message}");
                _writer = null;
                return null;
            }

            _hands = FindHands();
            _elapsed = 0.0;
            _frames = 0;
            LastPath = path;

            WriteHeader();
            Debug.Log($"[capture] recording to {path}");
            return path;
        }

        /// <summary>Finish and close the file. Safe to call when not recording.</summary>
        public string Stop()
        {
            if (!IsRecording) return LastPath;

            try
            {
                _writer.Flush();
                _writer.Dispose();
            }
            catch (Exception e)
            {
                Debug.LogError($"[capture] closing the recording failed: {e.Message}");
            }

            _writer = null;
            Debug.Log($"[capture] wrote {_frames} frames ({_elapsed:F1}s) to {LastPath}");
            return LastPath;
        }

        void Update()
        {
            if (!IsRecording) return;

            _elapsed += Time.unscaledDeltaTime;
            WriteFrame();
        }

        // --- Header -----------------------------------------------------------

        /// <summary>
        /// A JSON line naming every channel in this recording, then the frames.
        /// </summary>
        /// <remarks>
        /// Self-describing on purpose: which channels exist depends on the
        /// headset, so a reader that assumed a fixed layout would misparse every
        /// file made on different hardware. The header is text so the layout can
        /// be read with `head -1` without a parser.
        /// </remarks>
        void WriteHeader()
        {
            var channels = new List<string>
            {
                Channel("head", "pose", 1),
                Channel("leftHand", "pose", HandJointCount),
                Channel("rightHand", "pose", HandJointCount),
            };

            channels.AddRange(MetaChannels());

            var header = "{\"format\":\"auralperf\""
                       + $",\"version\":{FormatVersion}"
                       + $",\"startedUtc\":\"{DateTime.UtcNow:yyyy-MM-ddTHH:mm:ssZ}\""
                       + $",\"device\":\"{Escape(SystemInfo.deviceModel)}\""
                       + $",\"channels\":[{string.Join(",", channels)}]"
                       + ",\"frame\":\"uint16 byteLength, float32 t, channels in order, "
                       + "uint8 noteCount, noteCount x (uint8 pitch, uint8 velocity)\"}";

            var bytes = Encoding.UTF8.GetBytes(header + "\n");
            _writer.Write(bytes);
        }

        static string Channel(string name, string kind, int count) =>
            $"{{\"name\":\"{name}\",\"kind\":\"{kind}\",\"count\":{count}}}";

        static string Escape(string s) =>
            string.IsNullOrEmpty(s) ? "" : s.Replace("\\", "\\\\").Replace("\"", "\\\"");

        // --- Frames -----------------------------------------------------------

        /// <summary>
        /// One frame, length-prefixed.
        /// </summary>
        /// <remarks>
        /// The length prefix is what makes a truncated file usable. A recording
        /// ends when the battery dies or the app is killed at least as often as
        /// it ends with a clean Stop, and without a prefix the reader cannot tell
        /// a short final frame from a corrupt one — so the whole take is lost for
        /// the sake of its last 14 milliseconds.
        /// </remarks>
        void WriteFrame()
        {
            _frameBuffer.SetLength(0);
            using var frame = new BinaryWriter(_frameBuffer, Encoding.UTF8, leaveOpen: true);

            frame.Write((float)_elapsed);

            var head = Camera.main;
            WritePose(frame, head != null
                ? new Pose(head.transform.position, head.transform.rotation)
                : default);

            WriteHand(frame, leftHand: true);
            WriteHand(frame, leftHand: false);
            WriteMetaChannels(frame);

            // The notes actually sounding, so the capture can be lined up against
            // the performance without trusting two clocks to agree.
            var held = link != null ? link.HeldNotes : null;
            var count = held != null ? Mathf.Min(held.Count, byte.MaxValue) : 0;
            frame.Write((byte)count);
            for (var i = 0; i < count; i++)
            {
                frame.Write(held[i].pitch);
                frame.Write(held[i].velocity);
            }

            frame.Flush();

            var length = (int)_frameBuffer.Length;
            if (length > ushort.MaxValue)
            {
                // Cannot happen with the channels above, but silently writing a
                // truncated length would corrupt every frame after this one.
                Debug.LogError($"[capture] frame of {length} bytes exceeds the length prefix; stopping");
                Stop();
                return;
            }

            _writer.Write((ushort)length);
            _writer.Write(_frameBuffer.GetBuffer(), 0, length);
            _frames++;
        }

        static void WritePose(BinaryWriter frame, Pose pose)
        {
            frame.Write(pose.position.x);
            frame.Write(pose.position.y);
            frame.Write(pose.position.z);
            frame.Write(pose.rotation.x);
            frame.Write(pose.rotation.y);
            frame.Write(pose.rotation.z);
            frame.Write(pose.rotation.w);
        }

        void WriteHand(BinaryWriter frame, bool leftHand)
        {
            var hand = _hands != null && _hands.running
                ? (leftHand ? _hands.leftHand : _hands.rightHand)
                : default;

            // An untracked hand still writes its joints, as identity poses. The
            // frame layout is fixed by the header, so a channel that sometimes
            // occupies no bytes would desynchronise the whole file.
            for (var id = XRHandJointID.BeginMarker; id < XRHandJointID.EndMarker; id++)
            {
                var pose = default(Pose);
                if (hand.isTracked && hand.GetJoint(id).TryGetPose(out var joint)) pose = joint;
                WritePose(frame, pose);
            }
        }

        static XRHandSubsystem FindHands()
        {
            var loader = XRGeneralSettings.Instance?.Manager?.activeLoader;
            return loader?.GetLoadedSubsystem<XRHandSubsystem>();
        }
    }
}
