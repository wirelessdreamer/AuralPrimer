// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// What the player frets on a MIDI guitar, drawn on the real neck: the marks
// the desktop's guitar views draw (visualizers/viz-tab/src/guitarViews.ts,
// drawLiveMarks), in the same colours and judged the same way. The strings
// arrive placed on the neck already (FRETS datagram); nothing here maps a
// MIDI channel to a string.

using AuralPrimer.Link;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public sealed partial class GuitarViews
    {
        /// <summary>On the chart's string and fret.</summary>
        static readonly Color LiveHit = Hex(0x34d399);
        /// <summary>The right pitch, somewhere other than where the chart has it.</summary>
        static readonly Color LiveOther = Hex(0xf59e0b);
        /// <summary>A note the chart does not want now.</summary>
        static readonly Color LiveOff = Hex(0xcbd5e1);
        /// <summary>A string fretted but not picked: where the hand is.</summary>
        static readonly Color LiveFretted = Hex(0x94a3b8);

        /// <summary>How early a note may be played and still count, and how late after it stops ringing (seconds).</summary>
        const float LiveJudgeSlack = 0.08f;

        /// <summary>
        /// A ring at each string the player is sounding, judged against the
        /// chart; a dashed one where a string is only fretted. Drawn over the
        /// chart's own dots, so it reads as "you" rather than "the song".
        /// </summary>
        void DrawLive(float now, float visibility)
        {
            if (link == null) return;
            var frets = link.HeldFrets;
            if (frets.Count == 0) return;
            var camera = Camera.main;
            for (var i = 0; i < frets.Count; i++)
            {
                var f = frets[i];
                if (f.String >= _profile.stringCount) continue;
                var position = NeckPoint(f.String, f.Fret);
                var radius = DotRadius(f.Fret);
                if (!f.Picked)
                {
                    Sprite(position, radius * 2.9f, DotTexture(DotKind.Dashed), LiveFretted, 0.75f * visibility, camera, 0.0010f);
                    continue;
                }
                var colour = JudgeLive(now, f);
                Sprite(position, radius * 4.2f, DotTexture(DotKind.Disc), colour, 0.22f * visibility, camera, 0.0009f);
                Sprite(position, radius * 3.1f, DotTexture(DotKind.Ring), colour, visibility, camera, 0.0011f);
            }
        }

        /// <summary>
        /// Hit on the chart's string and fret, other for the right pitch played
        /// somewhere else, off for neither: the desktop's judgeLive.
        /// </summary>
        Color JudgeLive(float now, MrProtocol.LiveFret f)
        {
            var notes = _chart.Notes;
            // A bass part sounds an octave below a guitar's same string and fret.
            var lift = _profile.isBass ? 12 : 0;
            var other = false;
            for (var i = _cursor; i < notes.Count; i++)
            {
                var n = notes[i];
                if (n.On > now + LiveJudgeSlack) break;
                if (now < n.On - LiveJudgeSlack || now > Mathf.Max(n.Off, n.On + 0.14f) + LiveJudgeSlack) continue;
                if (n.String == f.String && n.Fret == f.Fret) return LiveHit;
                if (n.Pitch + lift == f.Pitch) other = true;
            }
            return other ? LiveOther : LiveOff;
        }
    }
}
