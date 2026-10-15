/**
 * One run of an expensive async load, however many callers ask for it.
 *
 * Made for the piano pack, which is loaded on the first live note: a 64 MB
 * pack decoded into memory, and the audio engine brought up with it. Without
 * this, every note that arrived while the first load was still running
 * started another one -- a strummed MIDI guitar sends dozens a second, and
 * the overlapping loads exhausted memory and locked the machine up. A load
 * that fails (no pack installed, audio not ready) is not retried on every
 * note either, only once `retryAfterMs` has passed.
 */
export function singleFlight(
  load: () => Promise<boolean>,
  opts: { retryAfterMs: number; now?: () => number },
): () => Promise<boolean> {
  const now = opts.now ?? (() => performance.now());
  let done = false;
  let inFlight: Promise<boolean> | null = null;
  let failedAt = -Infinity;

  return () => {
    if (done) return Promise.resolve(true);
    if (inFlight) return inFlight;
    if (now() - failedAt < opts.retryAfterMs) return Promise.resolve(false);
    inFlight = load()
      .catch(() => false)
      .then((ok) => {
        inFlight = null;
        if (ok) done = true;
        else failedAt = now();
        return ok;
      });
    return inFlight;
  };
}
