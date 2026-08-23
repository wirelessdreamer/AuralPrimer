// Copyright 2026 Nathanael Anderson. Licensed under the Apache License 2.0.
//
// A spatial anchor for the calibrated keyboard.
//
// Calibration used to be stored as raw world coordinates, which is a guess
// dressed up as a measurement: the headset re-localises between sessions, so
// the same numbers describe a different place in the room next time. The
// keyboard would come back across the room or through the floor, and the wizard
// would skip calibration because a profile existed, leaving nothing on screen
// and no way to tell why.
//
// A spatial anchor is the platform's own answer to this. The runtime persists
// it, re-localises it against the room on the next session, and reports where
// it actually is — so restore is known rather than assumed. Everything is then
// stored relative to the anchor, and the anchor's transform carries it.
//
// Uses AR Foundation's anchor API, which the Meta OpenXR provider implements on
// Quest. That is the same machinery MRUK anchors sit on, without taking a
// dependency on the Meta XR SDK for a feature already present here.

using System;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.ARSubsystems;

namespace AuralPrimer.Calibration
{
    [AddComponentMenu("AuralPrimer/Keyboard Anchor")]
    public sealed class KeyboardAnchor : MonoBehaviour
    {
        [SerializeField] ARAnchorManager anchorManager;

        ARAnchor _anchor;
        CancellationTokenSource _cancel;

        /// <summary>The anchor's transform, or null if there is no anchor yet.
        /// Everything about the keyboard is positioned relative to this.</summary>
        public Transform Space => _anchor != null ? _anchor.transform : null;

        /// <summary>Is there a localised anchor to hang the keyboard off?</summary>
        public bool IsAnchored => _anchor != null;

        void Awake()
        {
            if (anchorManager == null) anchorManager = FindFirstObjectByType<ARAnchorManager>();
        }

        void OnDestroy()
        {
            _cancel?.Cancel();
            _cancel?.Dispose();
        }

        /// <summary>
        /// Anchor the keyboard at <paramref name="pose"/> and persist it.
        /// </summary>
        /// <returns>The anchor's persistent id, or null if it could not be saved.</returns>
        public async Task<string> CreateAndSaveAsync(Pose pose)
        {
            if (anchorManager == null)
            {
                Debug.LogError("[anchor] no ARAnchorManager; cannot anchor the keyboard");
                return null;
            }

            RemoveExisting();

            var created = await anchorManager.TryAddAnchorAsync(pose);
            if (!created.status.IsSuccess())
            {
                Debug.LogError($"[anchor] could not create an anchor: {created.status.nativeStatusCode}");
                return null;
            }

            _anchor = created.value;

            _cancel?.Dispose();
            _cancel = new CancellationTokenSource();

            var saved = await anchorManager.TrySaveAnchorAsync(_anchor, _cancel.Token);
            if (!saved.status.IsSuccess())
            {
                // The anchor still works for this session; only persistence
                // failed, so say which of the two is missing rather than
                // implying the keyboard is unusable now.
                Debug.LogWarning($"[anchor] anchored, but saving failed: {saved.status.nativeStatusCode}. "
                               + "Calibration will not survive a restart.");
                return null;
            }

            // SerializableGuid.ToString() writes "{low:X16}-{high:X16}", which is
            // not a GUID string and cannot be parsed back as one — saving that
            // form meant every restore failed and the user re-calibrated on every
            // launch, even without moving. Store the real GUID it wraps.
            var id = saved.value.guid.ToString();
            Debug.Log($"[anchor] keyboard anchored and saved as {id}");
            return id;
        }

        /// <summary>
        /// Restore a previously saved anchor. The runtime re-localises it, so
        /// the pose returned is where the keyboard genuinely is now.
        /// </summary>
        public async Task<bool> TryLoadAsync(string id)
        {
            if (anchorManager == null || string.IsNullOrEmpty(id)) return false;
            if (!Guid.TryParse(id, out var guid))
            {
                Debug.LogWarning($"[anchor] '{id}' is not a usable anchor id");
                return false;
            }

            RemoveExisting();

            _cancel?.Dispose();
            _cancel = new CancellationTokenSource();

            var loaded = await anchorManager.TryLoadAnchorAsync(new SerializableGuid(guid), _cancel.Token);
            if (!loaded.status.IsSuccess())
            {
                // Expected and recoverable: the room may be unrecognisable, or
                // the anchor may have been cleared on the device.
                Debug.Log($"[anchor] {id} could not be re-localised "
                        + $"({loaded.status.nativeStatusCode}); re-calibration needed");
                return false;
            }

            _anchor = loaded.value;
            Debug.Log($"[anchor] re-localised {id} at {_anchor.transform.position}");
            return true;
        }

        /// <summary>Forget the anchor, on device as well as in this session.</summary>
        public async Task EraseAsync(string id)
        {
            RemoveExisting();

            if (anchorManager == null || string.IsNullOrEmpty(id)) return;
            if (!Guid.TryParse(id, out var guid)) return;

            _cancel?.Dispose();
            _cancel = new CancellationTokenSource();
            await anchorManager.TryEraseAnchorAsync(new SerializableGuid(guid), _cancel.Token);
        }

        void RemoveExisting()
        {
            if (_anchor == null) return;
            Destroy(_anchor.gameObject);
            _anchor = null;
        }
    }
}
