import { act, fireEvent, render, screen } from "@testing-library/react";

import { ServiceCallPanel } from "@/emulator/service-call";
import type { ServiceCallOut } from "@/emulator/service-call-model";

const call: ServiceCallOut = {
  id: "c1",
  service: "mosgaz",
  service_title: "Мосгаз",
  kind: "outgoing",
  contact: "начальник аварийной бригады",
  started_at: "2026-10-21T08:40:00Z",
  answered: true,
  answered_at: "2026-10-21T08:40:01Z",
  ended_at: null,
  end_reason: null,
  telephony: false,
  seconds: null,
  facts_passed: [],
  facts_required: [],
  recording_available: false,
  turns: [],
};

function panel(onSpeak = vi.fn()) {
  return (
    <ServiceCallPanel
      dialing={false}
      call={call}
      telephony={false}
      sttAvailable
      micDeviceId={null}
      devices={[]}
      onMicDevice={() => {}}
      onMicOpened={() => {}}
      pending={false}
      error={null}
      onSay={() => {}}
      onSpeak={onSpeak}
      onAnswer={() => {}}
      cloud="off"
      onEnd={() => {}}
    />
  );
}

class FakeRecorder {
  static isTypeSupported = () => true;
  mimeType = "audio/webm";
  state = "inactive";
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["x"]) });
    this.onstop?.();
  }
}

function micAnswers(answer: () => Promise<MediaStream>) {
  vi.stubGlobal("navigator", { ...navigator, mediaDevices: { getUserMedia: vi.fn(answer) } });
}

const stream = () => ({ getTracks: () => [{ stop: () => {} }] }) as unknown as MediaStream;
const refused = (name: string) => () => Promise.reject(Object.assign(new Error(name), { name }));

describe("ServiceCallPanel microphone on the first try", () => {
  beforeEach(() => {
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    vi.stubGlobal("AudioContext", undefined);
    HTMLElement.prototype.setPointerCapture = () => {};
  });
  afterEach(() => vi.unstubAllGlobals());

  it("says the microphone is on, not «too short», when the button was released at the browser's question", async () => {
    let allow: (s: MediaStream) => void = () => {};
    micAnswers(() => new Promise((resolve) => (allow = resolve)));
    const onSpeak = vi.fn();
    render(panel(onSpeak));
    const talk = screen.getByTestId("service-call-talk");

    fireEvent.pointerDown(talk, { pointerId: 1 });
    await act(() => new Promise((r) => setTimeout(r, 400)));
    expect(screen.getByRole("status")).toHaveTextContent("нажмите «Разрешить»");
    fireEvent.pointerUp(talk, { pointerId: 1 }); // to click «Разрешить»
    await act(async () => allow(stream()));

    expect(screen.getByRole("status")).toHaveTextContent("Микрофон включён. Теперь зажмите кнопку");
    expect(screen.queryByText(/Слишком короткая/)).toBeNull();
    expect(onSpeak).not.toHaveBeenCalled();
  });

  it("tells where to allow the microphone when the browser refused it", async () => {
    micAnswers(refused("NotAllowedError"));
    render(panel());
    fireEvent.pointerDown(screen.getByTestId("service-call-talk"), { pointerId: 1 });
    expect(await screen.findByRole("alert")).toHaveTextContent("значок слева от адреса сайта");
  });

  it("says there is no microphone instead of asking to allow it", async () => {
    micAnswers(refused("NotFoundError"));
    render(panel());
    fireEvent.pointerDown(screen.getByTestId("service-call-talk"), { pointerId: 1 });
    expect(await screen.findByRole("alert")).toHaveTextContent("Микрофон не найден. Подключите гарнитуру");
  });
});
