import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// A stand-in for the browser's audio: counts the notes started.
const started: number[] = [];
class FakeParam {
  value = 0;
  setValueAtTime() {}
  linearRampToValueAtTime() {}
}
class FakeNode {
  gain = new FakeParam();
  frequency = new FakeParam();
  connect(node: FakeNode) {
    return node;
  }
  disconnect() {}
  start(at: number) {
    started.push(at);
  }
  stop() {}
}
class FakeAudioContext {
  state = "running";
  currentTime = 0;
  destination = new FakeNode();
  createGain() {
    return new FakeNode();
  }
  createOscillator() {
    return new FakeNode();
  }
  resume() {
    return Promise.resolve();
  }
}

async function tones() {
  vi.resetModules();
  return import("@/emulator/phone-tones");
}

describe("phone tones", () => {
  beforeEach(() => {
    started.length = 0;
    vi.useFakeTimers();
    vi.stubGlobal("AudioContext", FakeAudioContext);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("beeps twice before the other side answers", async () => {
    const { playRingback, RINGBACK_MS } = await tones();
    playRingback(RINGBACK_MS);
    expect(started).toEqual([0, 1.5]);
  });

  it("rings once per call and never over another sound", async () => {
    const { ringOnce } = await tones();
    ringOnce("a");
    const ring = started.length;
    expect(ring).toBeGreaterThan(0);
    ringOnce("b"); // a second squad while the first rings: silent
    ringOnce("a"); // the same call shown again: silent
    expect(started.length).toBe(ring);
    vi.advanceTimersByTime(2500);
    ringOnce("a");
    ringOnce("b");
    expect(started.length).toBe(ring);
    ringOnce("c"); // a new call after the ring ended rings
    expect(started.length).toBe(2 * ring);
  });

  it("does not ring over the beeps of a call being made", async () => {
    const { playRingback, ringOnce } = await tones();
    const stop = playRingback();
    const beeps = started.length;
    ringOnce("a");
    expect(started.length).toBe(beeps);
    stop();
    ringOnce("b");
    expect(started.length).toBeGreaterThan(beeps);
  });
});
