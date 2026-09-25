import { render, screen } from "@testing-library/react";

import { ServiceCallPanel } from "@/emulator/service-call";
import type { ServiceCallOut } from "@/emulator/service-call-model";

const call: ServiceCallOut = {
  id: "call-1",
  service: "mosgaz",
  service_title: "Мосгаз",
  kind: "outgoing",
  started_at: "2026-09-25T08:40:00Z",
  answered: true,
  answered_at: "2026-09-25T08:40:01Z",
  ended_at: null,
  end_reason: null,
  telephony: false,
  seconds: null,
  facts_passed: [],
  facts_required: [],
  recording_available: false,
  turns: [
    {
      index: 0,
      role: "caller",
      text: "Дежурный Мосгаза, слушаю.",
      topics: [],
      audio_url: null,
      heard: false,
      generated: false,
    },
  ],
};

function ended(call: ServiceCallOut, reason: string): ServiceCallOut {
  return { ...call, ended_at: "2026-09-25T08:41:00Z", end_reason: reason };
}

function panel(
  replying: boolean,
  cloud: "off" | "live" | "failed" = "off",
  ended = false,
) {
  return (
    <ServiceCallPanel
      call={ended ? { ...call, ended_at: "2026-09-25T08:41:00Z" } : call}
      telephony={false}
      sttAvailable={false}
      micDeviceId={null}
      devices={[]}
      onMicDevice={() => {}}
      onMicOpened={() => {}}
      pending={replying}
      replying={replying}
      error={null}
      onSay={() => {}}
      onSpeak={() => {}}
      onAnswer={() => {}}
      cloud={cloud}
      onEnd={() => {}}
    />
  );
}

describe("ServiceCallPanel while the answer is awaited", () => {
  it("shows who is answering until the answer comes", () => {
    const { rerender } = render(panel(true));
    expect(screen.getByRole("status")).toHaveTextContent("дежурный отвечает…");
    rerender(panel(false));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows nothing in a cloud call: the cloud voice answers by itself", () => {
    render(panel(true, "live"));
    expect(
      screen.queryByTestId("service-call-replying"),
    ).not.toBeInTheDocument();
  });

  it("shows nothing once the call is over", () => {
    render(panel(true, "off", true));
    expect(
      screen.queryByTestId("service-call-replying"),
    ).not.toBeInTheDocument();
  });
});

describe("ServiceCallPanel after the call to the own service", () => {
  function after(target: ServiceCallOut, awaitingReport: boolean) {
    return (
      <ServiceCallPanel
        call={target}
        telephony={false}
        sttAvailable={false}
        micDeviceId={null}
        devices={[]}
        onMicDevice={() => {}}
        onMicOpened={() => {}}
        pending={false}
        awaitingReport={awaitingReport}
        error={null}
        onSay={() => {}}
        onSpeak={() => {}}
        onAnswer={() => {}}
        cloud="off"
        onEnd={() => {}}
      />
    );
  }

  it("asks to wait for the report once the dispatcher hung up", () => {
    render(after(ended(call, "hangup"), true));
    expect(screen.getByTestId("await-report")).toHaveTextContent(
      "Ожидайте доклад",
    );
  });

  it("says nothing of a report when none is coming or the call did not end that way", () => {
    const { rerender } = render(after(ended(call, "hangup"), false));
    expect(screen.queryByTestId("await-report")).not.toBeInTheDocument();
    rerender(after(ended(call, "no_answer"), true));
    expect(screen.queryByTestId("await-report")).not.toBeInTheDocument();
    rerender(after(call, true));
    expect(screen.queryByTestId("await-report")).not.toBeInTheDocument();
  });
});
