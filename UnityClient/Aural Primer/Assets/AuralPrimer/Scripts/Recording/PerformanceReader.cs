// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Reads back a .auralperf file.
//
// Scrubbing is the reason this indexes rather than streams: frames are
// variable-length, so finding "the frame at 3:41" by reading forward from the
// start makes every drag of the scrub bar cost the whole file. One pass at load
// builds an offset per frame, after which any time in the take is a binary
// search away.
//
// The header is trusted for layout and nothing else. Frame lengths are checked
// against the bytes actually present, because the common way a recording ends
// is a dead battery mid-write, and a take that stops early should play up to
// where it stops rather than refuse to open.

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEngine;

namespace AuralPrimer.Recording
{
    public sealed class PerformanceReader
    {
        [Serializable]
        public sealed class Channel
        {
            public string name;
            public string kind;
            public int count;
        }

        [Serializable]
        public sealed class Header
        {
            public string format;
            public int version;
            public string startedUtc;
            public string device;
            public Channel[] channels;
        }

        /// <summary>One decoded frame. Reused across reads — copy if you keep it.</summary>
        public sealed class Frame
        {
            public float Time;
            public readonly Dictionary<string, Pose[]> Poses = new();
            public readonly Dictionary<string, float[]> Weights = new();
            public readonly List<(byte pitch, byte velocity)> Notes = new();
        }

        public Header Info { get; private set; }
        public int FrameCount => _offsets.Count;
        public float Duration => _times.Count > 0 ? _times[^1] : 0f;
        public string Path { get; private set; }

        byte[] _bytes;
        readonly List<int> _offsets = new();   // start of each frame's payload
        readonly List<int> _lengths = new();
        readonly List<float> _times = new();
        readonly Frame _frame = new();

        /// <summary>Load and index a recording. Returns null on anything unusable.</summary>
        public static PerformanceReader Load(string path)
        {
            try
            {
                var reader = new PerformanceReader { Path = path, _bytes = File.ReadAllBytes(path) };
                return reader.Index() ? reader : null;
            }
            catch (Exception e)
            {
                Debug.LogError($"[playback] cannot read {path}: {e.Message}");
                return null;
            }
        }

        bool Index()
        {
            var newline = Array.IndexOf(_bytes, (byte)'\n');
            if (newline <= 0)
            {
                Debug.LogError($"[playback] {Path} has no header line");
                return false;
            }

            Info = JsonUtility.FromJson<Header>(Encoding.UTF8.GetString(_bytes, 0, newline));
            if (Info == null || Info.format != "auralperf")
            {
                Debug.LogError($"[playback] {Path} is not an auralperf recording");
                return false;
            }

            if (Info.version != PerformanceCapture.FormatVersion)
            {
                // Not fatal by itself, but the frame layout is what the version
                // tracks, so playing it would draw a skeleton from misread bytes.
                Debug.LogError($"[playback] {Path} is format v{Info.version}, "
                             + $"this build reads v{PerformanceCapture.FormatVersion}");
                return false;
            }

            var at = newline + 1;
            while (at + 2 <= _bytes.Length)
            {
                var length = _bytes[at] | (_bytes[at + 1] << 8);
                at += 2;

                // A frame the file does not actually contain: the recording was
                // cut off mid-write. Everything before it is still good.
                if (length < 4 || at + length > _bytes.Length)
                {
                    Debug.Log($"[playback] {System.IO.Path.GetFileName(Path)} ends mid-frame "
                            + $"after {_offsets.Count} frames; playing what is there");
                    break;
                }

                _offsets.Add(at);
                _lengths.Add(length);
                _times.Add(BitConverter.ToSingle(_bytes, at));
                at += length;
            }

            if (_offsets.Count == 0)
            {
                Debug.LogError($"[playback] {Path} has no complete frames");
                return false;
            }

            Debug.Log($"[playback] {System.IO.Path.GetFileName(Path)}: {_offsets.Count} frames, "
                    + $"{Duration:F1}s, channels=[{ChannelSummary()}]");
            return true;
        }

        string ChannelSummary()
        {
            if (Info.channels == null) return "";
            var parts = new string[Info.channels.Length];
            for (var i = 0; i < Info.channels.Length; i++)
            {
                parts[i] = $"{Info.channels[i].name}:{Info.channels[i].count}";
            }
            return string.Join(" ", parts);
        }

        /// <summary>Index of the last frame at or before <paramref name="time"/>.</summary>
        public int IndexAt(float time)
        {
            // Binary search: a scrub drag hits this every frame, and the whole
            // point of the index is not walking the take to answer it.
            var low = 0;
            var high = _times.Count - 1;
            while (low < high)
            {
                var mid = (low + high + 1) / 2;
                if (_times[mid] <= time) low = mid; else high = mid - 1;
            }
            return low;
        }

        /// <summary>Decode a frame. The returned object is reused between calls.</summary>
        public Frame Read(int index)
        {
            if (index < 0 || index >= _offsets.Count) return null;

            var at = _offsets[index];
            var end = at + _lengths[index];

            _frame.Time = BitConverter.ToSingle(_bytes, at);
            at += 4;

            _frame.Poses.Clear();
            _frame.Weights.Clear();
            _frame.Notes.Clear();

            foreach (var channel in Info.channels)
            {
                if (channel.kind == "pose")
                {
                    var poses = new Pose[channel.count];
                    for (var i = 0; i < channel.count && at + 28 <= end; i++)
                    {
                        poses[i] = new Pose(
                            new Vector3(BitConverter.ToSingle(_bytes, at),
                                        BitConverter.ToSingle(_bytes, at + 4),
                                        BitConverter.ToSingle(_bytes, at + 8)),
                            new Quaternion(BitConverter.ToSingle(_bytes, at + 12),
                                           BitConverter.ToSingle(_bytes, at + 16),
                                           BitConverter.ToSingle(_bytes, at + 20),
                                           BitConverter.ToSingle(_bytes, at + 24)));
                        at += 28;
                    }
                    _frame.Poses[channel.name] = poses;
                }
                else
                {
                    var weights = new float[channel.count];
                    for (var i = 0; i < channel.count && at + 4 <= end; i++)
                    {
                        weights[i] = BitConverter.ToSingle(_bytes, at);
                        at += 4;
                    }
                    _frame.Weights[channel.name] = weights;
                }
            }

            if (at < end)
            {
                var notes = _bytes[at++];
                for (var i = 0; i < notes && at + 2 <= end; i++)
                {
                    _frame.Notes.Add((_bytes[at], _bytes[at + 1]));
                    at += 2;
                }
            }

            return _frame;
        }

        /// <summary>Recordings on this device, newest first.</summary>
        public static string[] List()
        {
            var directory = System.IO.Path.Combine(Application.persistentDataPath, "recordings");
            try
            {
                if (!Directory.Exists(directory)) return Array.Empty<string>();
                var files = Directory.GetFiles(directory, "*.auralperf");
                // Filenames are timestamps, so a plain descending sort is
                // newest-first without stat-ing every file.
                Array.Sort(files, StringComparer.Ordinal);
                Array.Reverse(files);
                return files;
            }
            catch (Exception e)
            {
                Debug.LogError($"[playback] cannot list {directory}: {e.Message}");
                return Array.Empty<string>();
            }
        }
    }
}
