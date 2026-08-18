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
        public const byte FramePing = 0x20;
        public const byte FramePong = 0x21;
        public const byte FrameTransport = 0x30;

        // UDP datagram tags
        public const byte DatagramPosition = 0x40;
        public const byte DatagramNotes = 0x41;

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
