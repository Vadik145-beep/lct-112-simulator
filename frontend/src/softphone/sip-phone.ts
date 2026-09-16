import JsSIP from "jssip";
import type { RTCSession } from "jssip/lib/RTCSession";
import type { IncomingRTCSessionEvent } from "jssip/lib/UA";

/**
 * The browser softphone (PRD 9.5): registers at Asterisk over WebSocket, takes the incoming
 * call of a training attempt, plays the caller through an `<audio>` element and reports the
 * microphone level and WebRTC statistics. One instance per signed-in trainee.
 */

export type RegistrationState = "disconnected" | "connecting" | "ready" | "failed";

export interface IncomingCall {
  attemptId: string | null; // from the X-Attempt-Id header of the INVITE
  callerNumber: string;
  callerName: string;
}

export interface CallStats {
  rttMs: number | null;
  jitterMs: number | null;
  packetsLost: number | null;
  codec: string | null;
}

export interface SipPhoneEvents {
  registration: (state: RegistrationState, detail?: string) => void;
  incoming: (call: IncomingCall) => void;
  talking: () => void;
  ended: (cause: string) => void;
  level: (level: number) => void; // 0..1, microphone
  stats: (stats: CallStats) => void;
}

const LEVEL_INTERVAL_MS = 100;
const STATS_INTERVAL_MS = 2000;
export const MIC_DEVICE_KEY = "softphone.mic";

function storedDebug(): string | null {
  try {
    return localStorage.getItem("debug");
  } catch {
    return null;
  }
}

export function storedMicDevice(): string | null {
  try {
    return localStorage.getItem(MIC_DEVICE_KEY);
  } catch {
    return null;
  }
}

export function storeMicDevice(deviceId: string | null) {
  try {
    if (deviceId) localStorage.setItem(MIC_DEVICE_KEY, deviceId);
    else localStorage.removeItem(MIC_DEVICE_KEY);
  } catch {
    /* private mode: the choice lives for the page only */
  }
}

/** Reads the microphone level of a stream through an analyser; call `stop` when done. */
export function watchLevel(stream: MediaStream, onLevel: (level: number) => void): () => void {
  const AudioContextCtor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextCtor) return () => {};
  const context = new AudioContextCtor();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  const buffer = new Uint8Array(analyser.fftSize);
  const timer = setInterval(() => {
    analyser.getByteTimeDomainData(buffer);
    let sum = 0;
    for (const value of buffer) {
      const centred = (value - 128) / 128;
      sum += centred * centred;
    }
    onLevel(Math.min(1, Math.sqrt(sum / buffer.length) * 4));
  }, LEVEL_INTERVAL_MS);
  return () => {
    clearInterval(timer);
    source.disconnect();
    void context.close();
  };
}

export async function readStats(pc: RTCPeerConnection): Promise<CallStats> {
  const result: CallStats = { rttMs: null, jitterMs: null, packetsLost: null, codec: null };
  const report = await pc.getStats();
  const codecs = new Map<string, string>();
  report.forEach((entry) => {
    if (entry.type === "codec") codecs.set(entry.id, (entry as RTCStats & { mimeType?: string }).mimeType ?? "");
  });
  report.forEach((entry) => {
    if (entry.type === "candidate-pair") {
      const pair = entry as RTCIceCandidatePairStats;
      if (pair.state === "succeeded" && pair.currentRoundTripTime !== undefined) {
        result.rttMs = Math.round(pair.currentRoundTripTime * 1000);
      }
    }
    if (entry.type === "inbound-rtp") {
      const inbound = entry as RTCInboundRtpStreamStats & { codecId?: string };
      if (inbound.jitter !== undefined) result.jitterMs = Math.round(inbound.jitter * 1000);
      if (inbound.packetsLost !== undefined) result.packetsLost = inbound.packetsLost;
      if (inbound.codecId) result.codec = (codecs.get(inbound.codecId) ?? "").replace("audio/", "") || null;
    }
  });
  return result;
}

export class SipPhone {
  private ua: JsSIP.UA | null = null;
  private session: RTCSession | null = null;
  private stopLevel: (() => void) | null = null;
  private statsTimer: ReturnType<typeof setInterval> | null = null;
  readonly audio: HTMLAudioElement;
  private listeners: Partial<SipPhoneEvents> = {};
  micDeviceId: string | null = storedMicDevice();

  constructor() {
    this.audio = document.createElement("audio");
    this.audio.autoplay = true;
    this.audio.setAttribute("data-softphone", "remote");
    document.body.appendChild(this.audio);
  }

  on<K extends keyof SipPhoneEvents>(event: K, handler: SipPhoneEvents[K]) {
    this.listeners[event] = handler;
  }

  private emit<K extends keyof SipPhoneEvents>(event: K, ...args: Parameters<SipPhoneEvents[K]>) {
    const handler = this.listeners[event] as ((...a: Parameters<SipPhoneEvents[K]>) => void) | undefined;
    handler?.(...args);
  }

  /** Registers at Asterisk; `wsUrl` is wss://<host>/ws/sip, credentials from GET /me/sip. */
  register(wsUrl: string, uri: string, password: string, displayName: string) {
    this.unregister();
    // JsSIP logs through the `debug` package: localStorage.debug = "JsSIP:*" turns it on.
    const debugNamespaces = storedDebug();
    if (debugNamespaces) JsSIP.debug.enable(debugNamespaces);
    else JsSIP.debug.disable();
    const socket = new JsSIP.WebSocketInterface(wsUrl);
    const ua = new JsSIP.UA({
      sockets: [socket],
      uri,
      password,
      display_name: displayName,
      register: true,
      register_expires: 120,
      session_timers: false,
      user_agent: "DDS-112 Trainer softphone",
    });
    ua.on("connecting", () => this.emit("registration", "connecting"));
    ua.on("registered", () => this.emit("registration", "ready"));
    ua.on("unregistered", () => this.emit("registration", "disconnected"));
    ua.on("registrationFailed", (e: { cause?: string }) =>
      this.emit("registration", "failed", e.cause ?? "регистрация отклонена"),
    );
    ua.on("disconnected", () => this.emit("registration", "disconnected"));
    ua.on("newRTCSession", (e: IncomingRTCSessionEvent) => this.onSession(e));
    this.ua = ua;
    ua.start();
  }

  unregister() {
    this.hangup();
    if (this.ua) {
      this.ua.stop();
      this.ua = null;
    }
  }

  destroy() {
    this.unregister();
    this.audio.remove();
  }

  private onSession(e: IncomingRTCSessionEvent) {
    if (e.originator !== "remote") return;
    if (this.session) {
      // One call at a time: a second INVITE is answered busy.
      e.session.terminate({ status_code: 486, reason_phrase: "Busy Here" });
      return;
    }
    const session = e.session;
    this.session = session;
    const identity = session.remote_identity;
    const call: IncomingCall = {
      attemptId: e.request.getHeader("X-Attempt-Id") ?? null,
      callerNumber: identity?.uri?.user ?? "",
      callerName: identity?.display_name ?? "",
    };
    session.on("peerconnection", (data: { peerconnection: RTCPeerConnection }) => {
      data.peerconnection.addEventListener("track", (ev) => {
        if (ev.streams[0]) this.audio.srcObject = ev.streams[0];
        else this.audio.srcObject = new MediaStream([ev.track]);
        void this.audio.play().catch(() => {});
      });
    });
    session.on("confirmed", () => {
      this.startLocalWatch();
      this.emit("talking");
    });
    session.on("accepted", () => this.startLocalWatch());
    session.on("ended", (data: { cause?: string }) => this.onEnded(data.cause ?? "ended"));
    session.on("failed", (data: { cause?: string }) => this.onEnded(data.cause ?? "failed"));
    this.emit("incoming", call);
  }

  get hasCall(): boolean {
    return this.session !== null;
  }

  /** Picks up the ringing call with the chosen microphone. */
  answer() {
    const session = this.session;
    if (!session) return;
    const audio: MediaTrackConstraints | boolean = this.micDeviceId
      ? { deviceId: { exact: this.micDeviceId } }
      : true;
    session.answer({
      mediaConstraints: { audio, video: false },
      pcConfig: { iceServers: [] },
      rtcAnswerConstraints: { offerToReceiveAudio: true, offerToReceiveVideo: false },
    });
  }

  hangup() {
    const session = this.session;
    if (!session) return;
    if (!session.isEnded()) session.terminate();
  }

  private startLocalWatch() {
    const pc = this.session?.connection;
    if (!pc) return;
    const track = pc.getSenders().find((s) => s.track?.kind === "audio")?.track;
    if (track && !this.stopLevel) {
      this.stopLevel = watchLevel(new MediaStream([track]), (level) => this.emit("level", level));
    }
    if (!this.statsTimer) {
      this.statsTimer = setInterval(() => {
        void readStats(pc).then((stats) => this.emit("stats", stats));
      }, STATS_INTERVAL_MS);
    }
  }

  private onEnded(cause: string) {
    this.stopLevel?.();
    this.stopLevel = null;
    if (this.statsTimer) clearInterval(this.statsTimer);
    this.statsTimer = null;
    this.session = null;
    this.audio.srcObject = null;
    this.emit("level", 0);
    this.emit("ended", cause);
  }
}
