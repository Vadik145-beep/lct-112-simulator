import { afterEach, describe, expect, it, vi } from "vitest";

import { CloudCall, MIC_BUSY, MIC_DENIED, MIC_MISSING, micProblem } from "@/softphone/cloud-call";

// Stand 19.10.2026: Vapi dropped eight calls in a row that gave it no sound; the trainee saw
// «соединение не установилось». The microphone is opened first and its trouble said plainly.

const keys = { public_key: "pk", api_url: "https://api.vapi.ai", assistant_id: "a", token: "t" };

function micError(name: string) {
  return Object.assign(new Error(name), { name });
}

describe("cloud call microphone", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("says plainly what is wrong with the microphone", () => {
    expect(micProblem(micError("NotAllowedError"))).toBe(MIC_DENIED);
    expect(micProblem(micError("NotFoundError"))).toBe(MIC_MISSING);
    expect(micProblem(micError("NotSupportedError"))).toBe(MIC_MISSING); // no microphone at all
    expect(micProblem(micError("NotReadableError"))).toBe(MIC_BUSY);
  });

  it("does not join the cloud when the browser refuses the microphone", async () => {
    const getUserMedia = vi.fn().mockRejectedValue(micError("NotAllowedError"));
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    const failed = vi.fn();
    const call = new CloudCall().on("failed", failed);
    await expect(call.start(keys as never, "headset")).rejects.toThrow();
    expect(failed).toHaveBeenCalledWith(MIC_DENIED);
    // The chosen microphone was asked for; refused, the default one is not tried.
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(getUserMedia).toHaveBeenNthCalledWith(1, { audio: { deviceId: { exact: "headset" } } });
  });

  it("falls back to the default microphone when the chosen one is gone", async () => {
    const getUserMedia = vi
      .fn()
      .mockRejectedValueOnce(micError("OverconstrainedError"))
      .mockRejectedValueOnce(micError("NotReadableError"));
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    const failed = vi.fn();
    await expect(new CloudCall().on("failed", failed).start(keys as never, "old")).rejects.toThrow();
    expect(getUserMedia).toHaveBeenNthCalledWith(2, { audio: true });
    expect(failed).toHaveBeenCalledWith(MIC_BUSY);
  });

  it("puts another microphone into a live call without calling again", async () => {
    const track = (id: string) => ({ id, stop: vi.fn() });
    const first = track("default");
    const second = track("headset");
    const getUserMedia = vi
      .fn()
      .mockResolvedValueOnce({ getAudioTracks: () => [first], getTracks: () => [first] })
      .mockResolvedValueOnce({ getAudioTracks: () => [second], getTracks: () => [second] });
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    const setInputDevicesAsync = vi.fn().mockResolvedValue({});
    const vapi = {
      getDailyCallObject: () => ({ setInputDevicesAsync }),
      stop: vi.fn(),
      removeAllListeners: vi.fn(),
    };
    const call = new CloudCall();
    // A joined call: the microphone opened at the start and the SDK in place.
    Object.assign(call, { vapi, live: true, microphone: await getUserMedia() });
    await call.switchMicrophone("headset");
    expect(getUserMedia).toHaveBeenNthCalledWith(2, { audio: { deviceId: { exact: "headset" } } });
    expect(setInputDevicesAsync).toHaveBeenCalledWith({ audioSource: second });
    expect(first.stop).toHaveBeenCalled();
    expect(second.stop).not.toHaveBeenCalled();
    await call.stop();
  });
});
