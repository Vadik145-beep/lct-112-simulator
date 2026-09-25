import { render, waitFor } from "@testing-library/react";

import { ServiceCallPanel } from "@/emulator/service-call";
import type { ServiceCallOut } from "@/emulator/service-call-model";

function makeCall(
  id: string,
  service: string,
  kind: ServiceCallOut["kind"],
  replies: string[],
  ended = false,
): ServiceCallOut {
  // The officer (role «caller») speaks first, the dispatcher answers after every reply.
  const turns = replies.flatMap((audio, n) => {
    const officer = {
      index: n * 2,
      role: "caller",
      text: `реплика ${n}`,
      topics: [],
      audio_url: audio,
      heard: false,
      generated: false,
    };
    return n === replies.length - 1
      ? [officer]
      : [
          officer,
          { ...officer, index: n * 2 + 1, role: "operator", audio_url: null },
        ];
  });
  return {
    id,
    service,
    service_title: service,
    kind,
    started_at: "2026-09-25T08:40:00Z",
    answered: true,
    answered_at: "2026-09-25T08:40:01Z",
    ended_at: ended ? "2026-09-25T08:42:00Z" : null,
    end_reason: ended ? "hangup" : null,
    telephony: false,
    seconds: ended ? 120 : null,
    facts_passed: [],
    facts_required: [],
    recording_available: false,
    turns,
  };
}

function panel(call: ServiceCallOut) {
  return (
    <ServiceCallPanel
      call={call}
      telephony={false}
      sttAvailable={false}
      micDeviceId={null}
      devices={[]}
      onMicDevice={() => {}}
      onMicOpened={() => {}}
      pending={false}
      error={null}
      onSay={() => {}}
      onSpeak={() => {}}
      onAnswer={() => {}}
      cloud="off"
      onEnd={() => {}}
    />
  );
}

describe("ServiceCallPanel voice", () => {
  const fetched: string[] = [];

  beforeEach(() => {
    fetched.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        fetched.push(url);
        return new Response(new Blob(["mp3"]), { status: 200 });
      }),
    );
    URL.createObjectURL = vi.fn(() => "blob:reply");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    // jsdom has no layout: the transcript scrolls to its last line.
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("plays the officer of the next call although the previous call was longer", async () => {
    // A callback to the caller of six replies (turns 0…10), then the gas service answers.
    const caller = makeCall("call-1", "caller", "caller", [
      "/c0.mp3",
      "/c1.mp3",
      "/c2.mp3",
      "/c3.mp3",
      "/c4.mp3",
      "/c5.mp3",
    ]);
    const { rerender } = render(panel(caller));
    await waitFor(() => expect(fetched).toEqual(["/c5.mp3"]));

    rerender(
      panel({
        ...caller,
        ended_at: "2026-09-25T08:42:00Z",
        end_reason: "hangup",
      }),
    );
    rerender(panel(makeCall("call-2", "mosgaz", "outgoing", ["/m0.mp3"])));
    await waitFor(() => expect(fetched).toEqual(["/c5.mp3", "/m0.mp3"]));

    rerender(
      panel(makeCall("call-2", "mosgaz", "outgoing", ["/m0.mp3", "/m1.mp3"])),
    );
    await waitFor(() =>
      expect(fetched).toEqual(["/c5.mp3", "/m0.mp3", "/m1.mp3"]),
    );
  });

  it("does not replay a call that was already heard when it is shown again", async () => {
    const first = makeCall("call-1", "mosgaz", "outgoing", ["/m0.mp3"], true);
    const second = makeCall("call-2", "police", "outgoing", ["/p0.mp3"]);
    const { rerender } = render(panel(first));
    await waitFor(() => expect(fetched).toEqual(["/m0.mp3"]));

    rerender(panel(second));
    await waitFor(() => expect(fetched).toEqual(["/m0.mp3", "/p0.mp3"]));

    rerender(panel(first));
    rerender(panel(second));
    // Give a replay the chance to start before checking it did not.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetched).toEqual(["/m0.mp3", "/p0.mp3"]);
  });
});
