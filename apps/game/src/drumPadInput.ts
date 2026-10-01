/**
 * Guitar Hero / Rock Band drum kits, read as gamepads.
 *
 * These kits present to Windows as ordinary HID gamepads: every pad and the
 * kick pedal is a button, and a velocity-sensitive pad reports a value rather
 * than a plain on/off. So no driver and no new dependency -- the WebView's
 * Gamepad API already sees them.
 *
 * Nothing here assumes a layout. The same physical kit enumerates its buttons
 * in different orders through different adapters -- an official Xbox receiver,
 * a Raphnet, a Santroller board -- so a hardcoded table would be right for one
 * player and silently wrong for the next. Pads are bound by hitting them, the
 * same way the mixed-reality kit learns its MIDI notes, and for the same
 * reason: the hardware knows, and the player should never have to look it up.
 *
 * Velocity depends on the kit and cannot be assumed either. Rock Band 1 has
 * none; Rock Band 4 and the Guitar Hero 5 / Band Hero / Warriors of Rock kits
 * do. An analog button reports its force in `value`, so that is used when it
 * says anything useful and a fixed velocity stands in when it does not.
 */

/** Lane ids as `drum_tab.json` spells them -- the vocabulary the charts use. */
export type DrumLaneId = string;

/** Which gamepad button plays which drum. */
export type DrumPadMap = Record<number, DrumLaneId>;

export type DrumPadHit = {
  lane: DrumLaneId;
  /** 1..127, from the button's force where the kit reports it. */
  velocity: number;
};

/**
 * A button pressed harder than this counts as struck.
 *
 * Analog pads rest a little above zero and a digital one reports exactly 1, so
 * this has to sit low enough to catch a soft hit and high enough to ignore a
 * pad that never quite settles. Rock Band pads rattle; a threshold at zero
 * turns that into a drum roll nobody played.
 */
const PRESS_THRESHOLD = 0.35;

/** What a kit that reports no force gets, being a plain button press. */
const DEFAULT_VELOCITY = 100;

export type DrumPadInputHandle = {
  /** Call once per frame. Returns the strikes since the last call. */
  poll: () => DrumPadHit[];
  /** Bind the next pad hit to this lane; null cancels. */
  learn: (lane: DrumLaneId | null) => void;
  /** The lane currently waiting to be bound, if any. */
  learning: () => DrumLaneId | null;
  getMap: () => DrumPadMap;
  setMap: (map: DrumPadMap) => void;
  /** Is a gamepad visible at all? */
  connected: () => boolean;
};

export function initDrumPadInput(
  onMapChanged: (map: DrumPadMap) => void,
  onLearned?: (lane: DrumLaneId, button: number) => void,
): DrumPadInputHandle {
  let map: DrumPadMap = {};
  let learningLane: DrumLaneId | null = null;

  /** Button values from the previous poll, per gamepad index, for edges. */
  const previous = new Map<number, number[]>();

  function pads(): Gamepad[] {
    // getGamepads can return nulls for disconnected slots, and is absent
    // outside a browser context -- a test, or an older WebView.
    const raw = typeof navigator !== "undefined" && navigator.getGamepads
      ? navigator.getGamepads()
      : [];
    return Array.from(raw).filter((g): g is Gamepad => g != null);
  }

  function poll(): DrumPadHit[] {
    const hits: DrumPadHit[] = [];

    for (const pad of pads()) {
      const before = previous.get(pad.index) ?? [];
      const now = pad.buttons.map((b) => (typeof b.value === "number" ? b.value : b.pressed ? 1 : 0));
      previous.set(pad.index, now);

      for (let i = 0; i < now.length; i++) {
        // Rising edge only. A pad held down is one strike, not one per frame --
        // the kick pedal in particular rests pressed on some kits.
        const struck = now[i] >= PRESS_THRESHOLD && (before[i] ?? 0) < PRESS_THRESHOLD;
        if (!struck) continue;

        if (learningLane !== null) {
          map = { ...map, [i]: learningLane };
          const bound = learningLane;
          learningLane = null;
          onMapChanged(map);
          onLearned?.(bound, i);
          // Consumed by the binding. Playing the note as well would fire the
          // drum the player was only trying to name.
          continue;
        }

        const lane = map[i];
        if (!lane) continue;
        // An analog pad resting at 1.0 is a digital button in disguise; either
        // way the force it reports is the best velocity available.
        const velocity = Math.max(1, Math.min(127, Math.round(now[i] * 127))) || DEFAULT_VELOCITY;
        hits.push({ lane, velocity });
      }
    }

    return hits;
  }

  return {
    poll,
    learn: (lane) => {
      learningLane = lane;
    },
    learning: () => learningLane,
    getMap: () => map,
    setMap: (next) => {
      map = { ...next };
    },
    connected: () => pads().length > 0,
  };
}
