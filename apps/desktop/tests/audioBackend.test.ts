import { clampLoop, clampToLoop } from "../src/audioBackend";

describe("audioBackend helpers", () => {
  it("clampLoop normalizes ordering and clamps negatives", () => {
    expect(clampLoop({ t0: -2, t1: 5 })).toEqual({ t0: 0, t1: 5 });
    expect(clampLoop({ t0: 10, t1: 3 })).toEqual({ t0: 3, t1: 10 });
  });

  it("clampToLoop clamps to loop bounds", () => {
    const loop = { t0: 2, t1: 4 };
    expect(clampToLoop(1, loop)).toBe(2);
    expect(clampToLoop(3, loop)).toBe(3);
    expect(clampToLoop(5, loop)).toBe(4);
  });

  it("clampToLoop is passthrough without loop", () => {
    expect(clampToLoop(1.25, undefined)).toBe(1.25);
  });
});
