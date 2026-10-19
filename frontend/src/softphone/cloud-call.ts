import type Vapi from "@vapi-ai/web";

import type { WebCallOut } from "@/api/telephony";

/**
 * The browser call to the cloud caller (plan/track-c-vapi.md, no telephony): WebRTC between
 * the trainee's browser and Vapi through the Vapi Web SDK. The backend keeps the caller's
 * assistant and the transcript; this class only carries the media and reports what the
 * SDK says. The SDK is loaded on first use, so the closed-contour build never touches it.
 *
 * The microphone is opened before the cloud is joined (stand 19.10.2026: Vapi drops a call that
 * gets no sound for 15 seconds, and it dropped eight in a row while the browser asked for the
 * microphone or listened to the wrong one). A problem with it is told in plain words.
 */

export interface CloudCallEvents {
  /** The call is connected: the caller speaks first. */
  started: () => void;
  /** The call is over (the caller hung up, the SDK stopped, or the connection dropped). */
  ended: () => void;
  /** The caller starts / stops talking. */
  speaking: (speaking: boolean) => void;
  /** The SDK could not start or keep the call; ``reason`` is said to the trainee. */
  failed: (reason: string) => void;
  /** The microphone level while the call is live (0..1). */
  level: (level: number) => void;
  /** The call is live but the microphone gives no sound yet: what to check. */
  warning: (hint: string) => void;
  /** The browser is asked for the microphone (``true``) / has given it (``false``). */
  asking: (asking: boolean) => void;
}

// What the trainee reads when the microphone stands in the way.
export const MIC_DENIED =
  "Браузер не дал доступ к микрофону. Нажмите на значок слева от адреса сайта, разрешите микрофон и позвоните снова.";
export const MIC_MISSING =
  "Микрофон не найден. Подключите гарнитуру или выберите другой микрофон ниже и позвоните снова.";
export const MIC_BUSY =
  "Микрофон занят другой программой (запись экрана, Zoom, Teams). Закройте её или выберите другой микрофон ниже и позвоните снова.";
export const MIC_SILENT =
  "Облако не услышало вас: с микрофона не шёл звук. Выберите другой микрофон ниже, проверьте, что он не выключен, и позвоните снова.";
export const MIC_ASK_HINT =
  "Браузер спрашивает доступ к микрофону — нажмите «Разрешить» в окошке вверху страницы.";
export const MIC_QUIET_HINT =
  "Вас не слышно: с микрофона не идёт звук. Скажите что-нибудь или выберите другой микрофон ниже — он включится сразу.";

// A level under this is silence (the SDK reports 0..1).
const SILENT_LEVEL = 0.02;
// Vapi drops a silent call after 15 s: the trainee is warned well before.
const QUIET_WARNING_MS = 7000;

/** Plain words for a microphone that would not open (``getUserMedia`` errors). */
export function micProblem(error: unknown): string {
  const name = (error as { name?: string } | null)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError") return MIC_DENIED;
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "NotSupportedError") {
    return MIC_MISSING;
  }
  if (name === "NotReadableError" || name === "AbortError") return MIC_BUSY;
  return MIC_BUSY;
}

/** The microphone chosen in the phone (the system default when it is gone or not chosen). */
async function openMicrophone(deviceId: string | null): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw Object.assign(new Error("no media devices"), { name: "NotFoundError" });
  }
  if (deviceId) {
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: { deviceId: { exact: deviceId } } });
    } catch (error) {
      const name = (error as { name?: string } | null)?.name;
      if (name === "NotAllowedError" || name === "SecurityError") throw error;
      /* the chosen device is unplugged: the default one */
    }
  }
  return navigator.mediaDevices.getUserMedia({ audio: true });
}

export class CloudCall {
  private vapi: Vapi | null = null;
  private listeners: Partial<CloudCallEvents> = {};
  private live = false;
  private stopped = false;
  private microphone: MediaStream | null = null;
  private loudest = 0;
  private quietTimer: ReturnType<typeof setTimeout> | null = null;

  on<E extends keyof CloudCallEvents>(event: E, listener: CloudCallEvents[E]): this {
    this.listeners[event] = listener;
    return this;
  }

  private emit<E extends keyof CloudCallEvents>(event: E, ...args: Parameters<CloudCallEvents[E]>) {
    const listener = this.listeners[event] as ((...a: unknown[]) => void) | undefined;
    listener?.(...args);
  }

  private goLive() {
    if (this.live) return;
    this.live = true;
    this.emit("started");
    this.quietTimer = setTimeout(() => {
      if (this.live && this.loudest < SILENT_LEVEL) this.emit("warning", MIC_QUIET_HINT);
    }, QUIET_WARNING_MS);
  }

  /** Opens the microphone, then starts the call with the keys the backend gave; resolves once
   * the SDK has joined. ``micDeviceId`` is the microphone chosen in the phone. */
  async start(keys: WebCallOut, micDeviceId: string | null = null): Promise<void> {
    let track: MediaStreamTrack | undefined;
    this.emit("asking", true);
    try {
      this.microphone = await openMicrophone(micDeviceId);
      track = this.microphone.getAudioTracks()[0];
    } catch (error) {
      this.emit("asking", false);
      this.emit("failed", micProblem(error));
      throw error;
    }
    this.emit("asking", false);
    if (this.stopped) {
      this.release();
      return;
    }
    const { default: VapiSdk } = await import("@vapi-ai/web");
    if (this.stopped) {
      this.release();
      return;
    }
    // avoidEval: Daily's call machine without eval(), so the CSP stays without 'unsafe-eval'.
    // audioSource: the microphone opened above, so the SDK asks the browser for nothing.
    const vapi = new VapiSdk(
      keys.public_key,
      keys.api_url,
      { avoidEval: true },
      track ? { audioSource: track } : undefined,
    );
    this.vapi = vapi;
    vapi.on("call-start", () => this.goLive());
    vapi.on("call-end", () => {
      const wasLive = this.live;
      const silent = this.loudest < SILENT_LEVEL;
      this.live = false;
      this.clearQuiet();
      this.release();
      if (this.stopped) this.emit("ended");
      // Vapi hangs up a call that heard nothing: say why, not «the call ended».
      else if (wasLive && silent) this.emit("failed", MIC_SILENT);
      else if (wasLive) this.emit("ended");
      else this.emit("failed", "соединение с облаком не установилось");
    });
    vapi.on("speech-start", () => this.emit("speaking", true));
    vapi.on("speech-end", () => this.emit("speaking", false));
    vapi.on("local-volume-level", (level: number) => {
      const value = Math.min(1, level);
      this.loudest = Math.max(this.loudest, value);
      this.emit("level", value);
    });
    vapi.on("call-start-failed", (event) => this.emit("failed", event.error || event.stage));
    vapi.on("error", (error: unknown) => {
      // The SDK reports non-fatal things here too (the Krisp noise filter could not load,
      // the level observer): the call goes on without them. Only a failure before the call
      // is up, and not about audio processing, means the cloud is unavailable.
      if (this.live || isAudioProcessingError(error)) return;
      this.emit("failed", describe(error));
    });
    try {
      const call = await vapi.start(keys.assistant_id, {
        variableValues: { callToken: keys.token },
      });
      if (!call) throw new Error("облако не приняло звонок");
    } catch (error) {
      this.emit("failed", describe(error));
      throw error;
    }
    // Joined: the media flows; "call-start" (the assistant is listening) may come later.
    if (!this.stopped) this.goLive();
  }

  get isLive(): boolean {
    return this.live;
  }

  /** Puts another microphone into the call on the go (stand 19.10.2026: the browser took a
   * microphone that was not even plugged in). The silence watch starts over for it. */
  async switchMicrophone(deviceId: string | null): Promise<void> {
    const vapi = this.vapi;
    const daily = vapi?.getDailyCallObject();
    if (!vapi || !daily || this.stopped) return;
    let next: MediaStream;
    try {
      next = await openMicrophone(deviceId);
    } catch (error) {
      this.emit("warning", micProblem(error));
      return;
    }
    const track = next.getAudioTracks()[0];
    if (!track || this.stopped) {
      next.getTracks().forEach((t) => t.stop());
      return;
    }
    try {
      await daily.setInputDevicesAsync({ audioSource: track });
    } catch (error) {
      next.getTracks().forEach((t) => t.stop());
      this.emit("warning", micProblem(error));
      return;
    }
    this.release();
    this.microphone = next;
    this.loudest = 0;
    this.clearQuiet();
    this.quietTimer = setTimeout(() => {
      if (this.live && this.loudest < SILENT_LEVEL) this.emit("warning", MIC_QUIET_HINT);
    }, QUIET_WARNING_MS);
  }

  setMuted(muted: boolean): void {
    this.vapi?.setMuted(muted);
  }

  private clearQuiet() {
    if (this.quietTimer) clearTimeout(this.quietTimer);
    this.quietTimer = null;
  }

  private release() {
    this.microphone?.getTracks().forEach((t) => t.stop());
    this.microphone = null;
  }

  /** Ends the call from our side; safe to call twice. */
  async stop(): Promise<void> {
    this.stopped = true;
    this.clearQuiet();
    const vapi = this.vapi;
    this.vapi = null;
    if (vapi) {
      try {
        await vapi.stop();
      } catch {
        /* the room is already gone */
      }
      vapi.removeAllListeners();
    }
    this.release();
  }
}

function isAudioProcessingError(error: unknown): boolean {
  const type = (error as { type?: string } | null)?.type ?? "";
  return (
    type.startsWith("audio-process") ||
    type.startsWith("local-audio-level") ||
    type === "audio-processor-error"
  );
}

function describe(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    const e = error as { message?: string; error?: { message?: string }; errorMsg?: string };
    return e.message ?? e.error?.message ?? e.errorMsg ?? "ошибка облака";
  }
  return "ошибка облака";
}
