// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// A pad unit's pads, worked out from three of its corners.
//
// A sampling pad is not a kit: its pads sit in fixed rows on one flat face, so
// placing each one by hand would measure eighteen times what three pinches
// already say. The Roland SPD-S is three columns of three -- a thin strip of
// edge triggers along the far side, then two rows of main pads -- so the rows
// are given as fractions of the depth rather than assumed equal.
//
// Three corners, not two. Two diagonal corners leave the grid free to spin
// about the line between them, and pinning that down would mean guessing where
// the player stands. The third corner fixes the face exactly, so the cues land
// on the pads however the unit is turned or tilted on its stand.

using System.Collections.Generic;
using UnityEngine;

namespace AuralPrimer.Calibration
{
    public static class PadGrid
    {
        public const int Columns = 3;

        /// <summary>
        /// The SPD-S, far to near: the edge-trigger strip, then the two rows of
        /// main pads.
        /// </summary>
        /// <remarks>
        /// The strip's share is an estimate from the unit's proportions, not a
        /// measurement. If its cues sit off the edge triggers in the headset,
        /// this is the number to change.
        /// </remarks>
        public static readonly float[] SpdsRows = { 0.15f, 0.425f, 0.425f };

        /// <summary>Smallest believable side of the pad area.</summary>
        public const float MinSideMetres = 0.10f;

        /// <summary>Largest believable side. An SPD-S is under half a metre across.</summary>
        public const float MaxSideMetres = 0.80f;

        /// <summary>How far off square the near corner may sit from the far edge.</summary>
        public const float MaxSkewDegrees = 25f;

        /// <summary>
        /// How much of a grid cell the cue fills. The corners are the outside
        /// edges of the corner pads, so each cell includes half of the gap to
        /// its neighbours.
        /// </summary>
        const float PadFill = 0.8f;

        /// <summary>Smallest cue drawn, kept clear of KitPiece.IsComplete's 1 cm floor.</summary>
        public const float MinRadiusMetres = 0.012f;

        public enum Problem
        {
            None,
            /// <summary>A side is shorter than any pad unit.</summary>
            TooSmall,
            /// <summary>A side is longer than any pad unit.</summary>
            TooLarge,
            /// <summary>The near corner is not roughly square to the far edge.</summary>
            Skewed,
            /// <summary>Left and right were swapped, which would mirror the grid.</summary>
            Mirrored,
        }

        /// <summary>
        /// Is this a pad unit seen from the front?
        /// </summary>
        /// <param name="towardPlayer">Any vector from the unit toward the
        /// player's head. Only its side of the face matters.</param>
        public static Problem Check(Vector3 farLeft, Vector3 farRight, Vector3 nearLeft, Vector3 towardPlayer)
        {
            var across = farRight - farLeft;
            var down = nearLeft - farLeft;
            var width = across.magnitude;
            if (width < MinSideMetres) return Problem.TooSmall;
            if (width > MaxSideMetres) return Problem.TooLarge;

            var rightDir = across / width;
            var depthVec = down - Vector3.Dot(down, rightDir) * rightDir;
            var depth = depthVec.magnitude;
            if (depth < MinSideMetres) return Problem.TooSmall;
            if (down.magnitude > MaxSideMetres) return Problem.TooLarge;

            if (!IsSquare(across, down))
            {
                // Swapping the first two corners puts the third square to the
                // far edge from the SECOND corner instead. Say which mistake it
                // was, rather than calling it a crooked pinch.
                // Facing the player, too: the near-RIGHT corner pinched third is
                // also square from the second corner, but turns the face away.
                var swappedEdge = farLeft - farRight;
                var swappedSide = nearLeft - farRight;
                return IsSquare(swappedEdge, swappedSide)
                    && Vector3.Dot(Vector3.Cross(swappedEdge, swappedSide), towardPlayer) > 0f
                    ? Problem.Mirrored
                    : Problem.Skewed;
            }

            // Seen from the front, far-left to far-right and then toward the
            // player turns the face up at them. Swap left and right and it turns
            // away: the grid would be the mirror image of the unit.
            var face = Vector3.Cross(rightDir, depthVec / depth);
            if (Vector3.Dot(face, towardPlayer) < 0f) return Problem.Mirrored;

            return Problem.None;
        }

        static bool IsSquare(Vector3 edge, Vector3 side) =>
            Mathf.Abs(90f - Vector3.Angle(edge, side)) <= MaxSkewDegrees;

        /// <summary>
        /// One piece per pad, row by row from the far left.
        /// </summary>
        /// <param name="ids">Lane ids, far row first, each row left to right:
        /// <see cref="Columns"/> per row.</param>
        /// <param name="rows">Each row's share of the depth, far to near.</param>
        /// <remarks>
        /// Notes are left unlearned (-1). They come from striking each pad, as
        /// they do for a kit, so a unit whose notes were changed still works.
        /// Call <see cref="Check"/> first: this assumes the corners make sense.
        /// </remarks>
        public static List<KitPiece> Build(Vector3 farLeft, Vector3 farRight, Vector3 nearLeft,
                                           IReadOnlyList<string> ids, IReadOnlyList<float> rows)
        {
            var across = farRight - farLeft;
            var width = across.magnitude;
            var rightDir = across / width;
            var down = nearLeft - farLeft;
            var depthVec = down - Vector3.Dot(down, rightDir) * rightDir;
            var depth = depthVec.magnitude;
            var depthDir = depthVec / depth;
            var normal = Vector3.Cross(rightDir, depthDir).normalized;

            var total = 0f;
            for (var row = 0; row < rows.Count; row++) total += rows[row];

            var cellWidth = width / Columns;
            var pieces = new List<KitPiece>(Columns * rows.Count);
            var rowStart = 0f;
            for (var row = 0; row < rows.Count; row++)
            {
                var rowDepth = depth * rows[row] / total;
                // Sized to its own row, so a thin edge strip gets a cue that
                // stays on the strip -- but never under MinRadiusMetres, below
                // which KitPiece.IsComplete would silently drop the pad.
                var radius = Mathf.Max(MinRadiusMetres, 0.5f * Mathf.Min(cellWidth, rowDepth) * PadFill);
                for (var column = 0; column < Columns; column++)
                {
                    var centre = farLeft
                               + rightDir * (cellWidth * (column + 0.5f))
                               + depthDir * (rowStart + rowDepth * 0.5f);
                    pieces.Add(new KitPiece
                    {
                        id = ids[row * Columns + column],
                        centre = centre,
                        rim = centre + rightDir * radius,
                        normal = normal,
                    });
                }
                rowStart += rowDepth;
            }
            return pieces;
        }
    }
}
