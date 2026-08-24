// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// The song library as the headset sees it, and the audio it sends to search by
// voice. See `docs/mr-link-protocol.md` §6 — that document is the contract, and
// the shapes below are exactly what it specifies.

using System;
using System.Globalization;
using System.Text;
using UnityEngine;

namespace AuralPrimer.Link
{
    /// <summary>One row of the host's library.</summary>
    /// <remarks>
    /// Parsed with <see cref="JsonUtility"/> rather than by hand-scanning, which
    /// is what the CHART payload does. CHART hand-scans because its notes are a
    /// bare array and JsonUtility cannot deserialise one without a wrapper type;
    /// a LIBRARY payload is a top-level object, so that objection does not apply
    /// and the parser that cannot silently mis-read a field wins.
    /// </remarks>
    [Serializable]
    public sealed class LibrarySong
    {
        public string songId;
        public string title;
        public string artist;
        public string genre;
        public double durationSec;

        /// <summary>Duration as m:ss, or empty when the host did not say.</summary>
        public string Length =>
            durationSec > 0.5
                ? $"{(int)(durationSec / 60)}:{((int)durationSec % 60):00}"
                : "";
    }

    /// <summary>One page of results, plus the facets to filter by.</summary>
    [Serializable]
    public sealed class LibraryPage
    {
        public int page;
        public int pageSize;
        public int total;

        /// <summary>Distinct across the whole library, not just this page, so
        /// the filter chips do not change as the user pages through.</summary>
        public string[] artists;
        public string[] genres;
        public LibrarySong[] items;

        public int PageCount => pageSize > 0 ? Mathf.Max(1, (total + pageSize - 1) / pageSize) : 1;

        public static LibraryPage Parse(string json)
        {
            if (string.IsNullOrEmpty(json)) return null;

            try
            {
                var page = JsonUtility.FromJson<LibraryPage>(json);
                if (page == null) return null;

                // JsonUtility leaves absent arrays null. Every caller would
                // otherwise need the same null check before every foreach.
                page.artists ??= Array.Empty<string>();
                page.genres ??= Array.Empty<string>();
                page.items ??= Array.Empty<LibrarySong>();
                return page;
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[mr-link] unreadable library page: {e.Message}");
                return null;
            }
        }
    }

    /// <summary>What to ask the host for.</summary>
    public struct LibraryQuery
    {
        public string Search;
        public string Artist;
        public string Genre;
        public int Page;
        public int PageSize;

        /// <summary>
        /// Serialised by hand, because absent and empty must stay different.
        /// </summary>
        /// <remarks>
        /// JsonUtility writes a null string as an empty one, and the host reads
        /// an empty facet as "no filter" — so a round trip through it would turn
        /// "artist: Bach" into "any artist" the moment the field was cleared and
        /// set again. Writing the JSON here keeps null meaning null.
        /// </remarks>
        public string ToJson()
        {
            var json = new StringBuilder(96);
            json.Append('{');
            Field(json, "search", Search);
            Field(json, "artist", Artist);
            Field(json, "genre", Genre);
            json.Append("\"page\":")
                .Append(Mathf.Max(0, Page).ToString(CultureInfo.InvariantCulture));
            json.Append(",\"pageSize\":")
                .Append(Mathf.Max(1, PageSize).ToString(CultureInfo.InvariantCulture));
            json.Append('}');
            return json.ToString();
        }

        static void Field(StringBuilder json, string name, string value)
        {
            json.Append('"').Append(name).Append("\":");
            if (string.IsNullOrWhiteSpace(value)) json.Append("null");
            else json.Append('"').Append(Escape(value)).Append('"');
            json.Append(',');
        }

        static string Escape(string text)
        {
            // A search box takes whatever the user types, and titles carry
            // apostrophes and the odd quote. Backslash first, or it escapes its
            // own replacements.
            return text
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\n", " ")
                .Replace("\r", " ")
                .Replace("\t", " ");
        }
    }

    /// <summary>What the host heard.</summary>
    [Serializable]
    public sealed class VoiceResult
    {
        public string text;
        public string error;

        public static VoiceResult Parse(string json)
        {
            if (string.IsNullOrEmpty(json)) return null;

            try
            {
                return JsonUtility.FromJson<VoiceResult>(json);
            }
            catch (Exception e)
            {
                Debug.LogWarning($"[mr-link] unreadable voice result: {e.Message}");
                return null;
            }
        }
    }

    /// <summary>Turns a recorded clip into the WAV the host expects.</summary>
    /// <remarks>
    /// The protocol pins the format — mono, 16 kHz, 16-bit PCM — rather than
    /// negotiating it. Resampling here costs one pass over a few seconds of
    /// audio and saves sending three times the bytes over Wi-Fi; the alternative
    /// is the host guessing at a sample rate it was never told, which is the
    /// class of failure the protocol document exists to prevent.
    /// </remarks>
    public static class VoiceWav
    {
        public const int SampleRate = 16000;

        /// <summary>Longest query the host accepts, per protocol §6.</summary>
        public const float MaxSeconds = 10f;

        public static byte[] Encode(float[] samples, int sourceRate)
        {
            if (samples == null || samples.Length == 0 || sourceRate <= 0)
            {
                return Array.Empty<byte>();
            }

            var mono = Resample(samples, sourceRate);
            var bytes = new byte[44 + mono.Length * 2];
            WriteHeader(bytes, mono.Length);

            var at = 44;
            foreach (var sample in mono)
            {
                // Clamp before scaling: a mic peak past 1.0 would wrap to
                // full-scale negative and read as a click.
                var clamped = Mathf.Clamp(sample, -1f, 1f);
                var pcm = (short)Mathf.RoundToInt(clamped * short.MaxValue);
                bytes[at++] = (byte)(pcm & 0xFF);
                bytes[at++] = (byte)((pcm >> 8) & 0xFF);
            }

            return bytes;
        }

        /// <summary>Linear resample to 16 kHz.</summary>
        /// <remarks>
        /// Deliberately not a windowed filter. The destination is a speech
        /// recogniser trained on far worse than the aliasing this introduces,
        /// and a proper resampler would be code to maintain for a difference
        /// nothing downstream can hear.
        /// </remarks>
        static float[] Resample(float[] source, int sourceRate)
        {
            if (sourceRate == SampleRate) return source;

            var length = (int)((long)source.Length * SampleRate / sourceRate);
            if (length <= 0) return Array.Empty<float>();

            var output = new float[length];
            var step = (double)source.Length / length;
            for (var i = 0; i < length; i++)
            {
                var at = i * step;
                var lower = (int)at;
                var upper = Mathf.Min(lower + 1, source.Length - 1);
                output[i] = Mathf.Lerp(source[lower], source[upper], (float)(at - lower));
            }
            return output;
        }

        static void WriteHeader(byte[] into, int sampleCount)
        {
            const int channels = 1;
            const int bitsPerSample = 16;
            var dataBytes = sampleCount * 2;

            Ascii(into, 0, "RIFF");
            Int32(into, 4, 36 + dataBytes);
            Ascii(into, 8, "WAVE");
            Ascii(into, 12, "fmt ");
            Int32(into, 16, 16);                                    // PCM header length
            Int16(into, 20, 1);                                     // PCM, uncompressed
            Int16(into, 22, channels);
            Int32(into, 24, SampleRate);
            Int32(into, 28, SampleRate * channels * bitsPerSample / 8);
            Int16(into, 32, channels * bitsPerSample / 8);
            Int16(into, 34, bitsPerSample);
            Ascii(into, 36, "data");
            Int32(into, 40, dataBytes);
        }

        static void Ascii(byte[] into, int at, string text)
        {
            for (var i = 0; i < text.Length; i++) into[at + i] = (byte)text[i];
        }

        static void Int32(byte[] into, int at, int value)
        {
            into[at] = (byte)(value & 0xFF);
            into[at + 1] = (byte)((value >> 8) & 0xFF);
            into[at + 2] = (byte)((value >> 16) & 0xFF);
            into[at + 3] = (byte)((value >> 24) & 0xFF);
        }

        static void Int16(byte[] into, int at, int value)
        {
            into[at] = (byte)(value & 0xFF);
            into[at + 1] = (byte)((value >> 8) & 0xFF);
        }
    }
}
