// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// Wire encoding for the MR link — the mirror of the Rust host's protocol.rs.
// See docs/mr-link-protocol.md, which is the authority for both sides.
//
// Deliberately free of UnityEngine so it can be exercised in a plain test
// runner: this is the layer where a byte-order or offset mistake produces
// nonsense that looks like a physics bug three layers up.

using System;
using System.Collections.Generic;
using System.Text;

namespace AuralPrimer.Link
{
    public static class MrProtocol
    {
        public const string Magic = "AURALPRIMER";
        public const int ProtocolVersion = 1;

        // Discovery
        public const string MulticastGroup = "239.255.61.88";
        public const int MulticastPort = 47761;

        // TCP frame types
        public const byte FrameHello = 0x01;
        public const byte FrameWelcome = 0x02;
        public const byte FrameChart = 0x10;
        public const byte FrameSongChanged = 0x11;

        // Optional frames (protocol §6). A host that does not implement them
        // omits the matching name from `features` in WELCOME, so the client can
        // hide the menu rather than wait forever for a reply that never comes.
        public const byte FrameLibraryRequest = 0x12;
        public const byte FrameLibrary = 0x13;
        public const byte FrameSelectSong = 0x14;
        public const byte FrameVoiceQuery = 0x15;
        public const byte FrameVoiceResult = 0x16;
        public const byte FrameKeyboardLayout = 0x17;
        /// <summary>
        /// The drum chart, in the tab's own lane vocabulary.
        /// </summary>
        /// <remarks>
        /// Its own frame rather than a role variant of CHART: a drum chart has
        /// no pitches, no key and no hands, and pushing it through the melodic
        /// shape would mean inventing a value for each of them.
        /// </remarks>
        public const byte FrameDrumChart = 0x18;
        /// <summary>The headset's kit map. Headset -> host.</summary>
        public const byte FrameKitLayout = 0x19;
        /// <summary>The part this headset plays (protocol §7b). Headset -> host.</summary>
        public const byte FrameInstrument = 0x1A;
        /// <summary>The desktop's note spacing and playback rate (§7c). Host -> headset.</summary>
        public const byte FrameSettings = 0x1B;

        /// <summary>Names used in the WELCOME `features` array.</summary>
        public const string FeatureLibrary = "library";
        public const string FeatureVoice = "voice";
        public const string FeatureDrums = "drums";
        public const string FeatureInstrument = "instrument";
        public const string FeatureSettings = "settings";
        public const byte FramePing = 0x20;
        public const byte FramePong = 0x21;
        public const byte FrameTransport = 0x30;

        // UDP datagram tags
        public const byte DatagramPosition = 0x40;
        public const byte DatagramNotes = 0x41;
        /// <summary>Drum strikes since the previous datagram.</summary>
        public const byte DatagramDrumHits = 0x42;

        /// A larger prefix is a protocol error: refuse rather than allocate on it.
        public const int MaxPayload = 16 * 1024 * 1024;

        public const int PositionLength = 18;

        // ---- Discovery -----------------------------------------------------

        public readonly struct Beacon
        {
            public readonly string HostIp;
            public readonly int SessionPort;
            public readonly string HostName;

            public Beacon(string hostIp, int sessionPort, string hostName)
            {
                HostIp = hostIp;
                SessionPort = sessionPort;
                HostName = hostName;
            }
        }

        /// Parse a beacon. Returns false for anything that is not ours — wrong
        /// magic, wrong version, malformed. Foreign traffic on a shared
        /// multicast group is expected, not exceptional, so this must never
        /// throw: it is called for every datagram that arrives.
        public static bool TryParseBeacon(string raw, out Beacon beacon)
        {
            beacon = default;
            if (string.IsNullOrEmpty(raw)) return false;

            var parts = raw.TrimEnd('\r', '\n').Split('|');
            if (parts.Length < 6) return false;
            if (parts[0] != Magic) return false;
            if (!int.TryParse(parts[1], out var version) || version != ProtocolVersion) return false;
            if (parts[2] != "BEACON") return false;
            if (!int.TryParse(parts[4], out var port)) return false;

            beacon = new Beacon(parts[3], port, parts[5]);
            return true;
        }

        public static bool TryParseAck(string raw, out int sessionPort)
        {
            sessionPort = 0;
            if (string.IsNullOrEmpty(raw)) return false;

            var parts = raw.TrimEnd('\r', '\n').Split('|');
            if (parts.Length < 4) return false;
            if (parts[0] != Magic) return false;
            if (!int.TryParse(parts[1], out var version) || version != ProtocolVersion) return false;
            if (parts[2] != "ACK") return false;
            return int.TryParse(parts[3], out sessionPort);
        }

        public static byte[] EncodeConnect(string clientName)
        {
            return Encoding.UTF8.GetBytes($"{Magic}|{ProtocolVersion}|CONNECT|{clientName}");
        }

        // ---- TCP framing ---------------------------------------------------

        /// Frame: len(u32 LE, payload only) | type(u8) | payload.
        public static byte[] EncodeFrame(byte frameType, byte[] payload)
        {
            payload ??= Array.Empty<byte>();
            var frame = new byte[5 + payload.Length];
            var len = BitConverter.GetBytes((uint)payload.Length);
            if (!BitConverter.IsLittleEndian) Array.Reverse(len);
            Buffer.BlockCopy(len, 0, frame, 0, 4);
            frame[4] = frameType;
            Buffer.BlockCopy(payload, 0, frame, 5, payload.Length);
            return frame;
        }

        public static bool TryDecodeFrameHeader(byte[] header, out int length, out byte frameType)
        {
            length = 0;
            frameType = 0;
            if (header == null || header.Length < 5) return false;

            var len = ReadUInt32LE(header, 0);
            if (len > MaxPayload) return false;

            length = (int)len;
            frameType = header[4];
            return true;
        }

        // ---- UDP datagrams -------------------------------------------------

        public readonly struct PositionSample
        {
            public readonly double SongTimeSec;
            public readonly ulong HostClockUs;
            public readonly bool Playing;

            public PositionSample(double songTimeSec, ulong hostClockUs, bool playing)
            {
                SongTimeSec = songTimeSec;
                HostClockUs = hostClockUs;
                Playing = playing;
            }
        }

        public static bool TryDecodePosition(byte[] data, int length, out PositionSample sample)
        {
            sample = default;
            if (data == null || length != PositionLength || data[0] != DatagramPosition) return false;

            sample = new PositionSample(
                ReadDoubleLE(data, 1),
                ReadUInt64LE(data, 9),
                (data[17] & 1) != 0);
            return true;
        }

        /// The complete set of currently-held notes — not deltas. A dropped
        /// note-off would otherwise leave a key lit indefinitely.
        public static bool TryDecodeNotes(byte[] data, int length, out ulong hostClockUs,
                                          List<(byte pitch, byte velocity)> into)
        {
            hostClockUs = 0;
            if (data == null || length < 10 || data[0] != DatagramNotes) return false;

            hostClockUs = ReadUInt64LE(data, 1);
            int count = data[9];
            // Reject a truncated tail rather than returning a partial chord: half
            // a chord lights the wrong keys, which is worse than dropping it.
            if (length != 10 + count * 2) return false;

            into.Clear();
            for (var i = 0; i < count; i++)
            {
                into.Add((data[10 + i * 2], data[11 + i * 2]));
            }
            return true;
        }

        /// <summary>Drum strikes seen since the previous datagram.</summary>
        /// <remarks>
        /// Events, unlike the held-note snapshot above, because a strike does
        /// not last: a note-on with an immediate note-off is over inside a
        /// frame, and a snapshot taken either side of it shows nothing at all.
        /// Each carries the host clock from when the host saw it, so the hit
        /// can be placed against the song rather than against the packet.
        /// </remarks>
        public static bool TryDecodeDrumHits(
            byte[] data, int length, List<(byte note, byte velocity, ulong hostClockUs)> into)
        {
            if (data == null || length < 2 || data[0] != DatagramDrumHits) return false;

            int count = data[1];
            // Rejected outright rather than half-read, for the same reason a
            // partial chord is: a truncated list would report strikes the
            // player never made.
            if (length != 2 + count * 10) return false;

            into.Clear();
            for (var i = 0; i < count; i++)
            {
                var at = 2 + i * 10;
                into.Add((data[at], data[at + 1], ReadUInt64LE(data, at + 2)));
            }
            return true;
        }

        // ---- Instrument and settings (protocol §7b, §7c) -------------------

        /// <summary>The INSTRUMENT payload: which part this headset plays.</summary>
        /// <remarks>
        /// A role is one of a handful of plain words (keys, guitar, bass, ...),
        /// so nothing here needs escaping beyond refusing the two characters
        /// that would break the object.
        /// </remarks>
        public static byte[] EncodeInstrument(string role)
        {
            var safe = (role ?? "").Replace("\\", "").Replace("\"", "");
            return Encoding.UTF8.GetBytes("{\"role\":\"" + safe + "\"}");
        }

        /// <summary>
        /// Read a SETTINGS payload. False when neither value is readable.
        /// </summary>
        /// <remarks>
        /// Every frame carries both values, but a field that is missing or not
        /// a positive number is taken as 1 rather than failing the frame: a
        /// spacing of zero would collapse the lane onto the play line, and a
        /// rate of zero would stop the clock, both worse than the default.
        /// </remarks>
        public static bool TryParseSettings(string json, out double noteSpacing, out double playbackRate)
        {
            var haveSpacing = TryReadNumber(json, "noteSpacing", out noteSpacing);
            var haveRate = TryReadNumber(json, "playbackRate", out playbackRate);
            if (!haveSpacing || noteSpacing <= 0 || double.IsNaN(noteSpacing) || double.IsInfinity(noteSpacing))
            {
                noteSpacing = 1.0;
            }
            if (!haveRate || playbackRate <= 0 || double.IsNaN(playbackRate) || double.IsInfinity(playbackRate))
            {
                playbackRate = 1.0;
            }
            return haveSpacing || haveRate;
        }

        /// <summary>A top-level number in a small JSON object, by key.</summary>
        static bool TryReadNumber(string json, string key, out double value)
        {
            value = 0;
            if (string.IsNullOrEmpty(json)) return false;
            var marker = "\"" + key + "\":";
            var at = json.IndexOf(marker, StringComparison.Ordinal);
            if (at < 0) return false;
            at += marker.Length;
            while (at < json.Length && json[at] == ' ') at++;
            var end = at;
            while (end < json.Length && (char.IsDigit(json[end]) || json[end] is '.' or '-' or '+' or 'e' or 'E'))
            {
                end++;
            }
            return double.TryParse(json.Substring(at, end - at),
                                   System.Globalization.NumberStyles.Float,
                                   System.Globalization.CultureInfo.InvariantCulture,
                                   out value);
        }

        // ---- Little-endian readers ----------------------------------------
        // Written by hand rather than via BitConverter so behaviour is identical
        // on a big-endian runtime; the wire format is LE regardless of host.

        public static uint ReadUInt32LE(byte[] b, int offset)
        {
            return (uint)(b[offset] | (b[offset + 1] << 8) | (b[offset + 2] << 16) | (b[offset + 3] << 24));
        }

        public static ulong ReadUInt64LE(byte[] b, int offset)
        {
            ulong lo = ReadUInt32LE(b, offset);
            ulong hi = ReadUInt32LE(b, offset + 4);
            return lo | (hi << 32);
        }

        public static double ReadDoubleLE(byte[] b, int offset)
        {
            return BitConverter.Int64BitsToDouble((long)ReadUInt64LE(b, offset));
        }

        public static byte[] WriteUInt64LE(ulong value)
        {
            var b = new byte[8];
            for (var i = 0; i < 8; i++) b[i] = (byte)(value >> (i * 8));
            return b;
        }
    }
}
