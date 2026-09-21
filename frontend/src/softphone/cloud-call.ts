import type Vapi from "@vapi-ai/web";

import type { WebCallOut } from "@/api/telephony";

/**
 * The browser call to the cloud caller (plan/track-c-vapi.md, no telephony): WebRTC between
 * the trainee's browser and Vapi through the Vapi Web SDK. The backend keeps the caller's
 * assistant and the transcript; this class only carries the media and reports what the
 * SDK says. The SDK is loaded on first use, so the closed-contour build never touches it.
 */

export interface CloudCallEvents {
  /** The call is connected: the caller speaks first. */
  started: () => void;
  /** The call is over (the caller hung up, the SDK stopped, or the connection dropped). */
  ended: () => void;
  /** The caller starts / stops talking. */
  speaking: (speaking: boolean) => void;
  /** The SDK could not start or keep the call. */
  failed: (reason: string) => void;
  /** The microphone level while the call is live (0..1). */
  level: (level: number) => void;
}

export class CloudCall {
  private vapi: Vapi | null = null;
  private listeners: Partial<CloudCallEvents> = {};
  private live = false;
  private stopped = false;

  on<E extends keyof CloudCallEvents>(event: E, listener: CloudCallEvents[E]): this {
    this.listeners[event] = listener;
    return this;
  }

  private emit<E extends keyof CloudCallEvents>(event: E, ...args: Parameters<CloudCallEvents[E]>) {
    const listener = this.listeners[event] as ((...a: unknown[]) => void) | undefined;
    listener?.(...args);
  }

  /** Starts the call with the keys the backend gave; resolves once the SDK has joined. */
  async start(keys: WebCallOut): Promise<void> {
    const { default: VapiSdk } = await import("@vapi-ai/web");
    if (this.stopped) return;
    // avoidEval: Daily's call machine without eval(), so the CSP stays without 'unsafe-eval'.
    const vapi = new VapiSdk(keys.public_key, keys.api_url, { avoidEval: true });
    this.vapi = vapi;
    vapi.on("call-start", () => {
      this.live = true;
      this.emit("started");
    });
    vapi.on("call-end", () => {
      const wasLive = this.live;
      this.live = false;
      if (wasLive || this.stopped) this.emit("ended");
      else this.emit("failed", "соединение с облаком не установилось");
    });
    vapi.on("speech-start", () => this.emit("speaking", true));
    vapi.on("speech-end", () => this.emit("speaking", false));
    vapi.on("local-volume-level", (level: number) => this.emit("level", Math.min(1, level)));
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
    if (!this.live && !this.stopped) {
      this.live = true;
      this.emit("started");
    }
  }

  get isLive(): boolean {
    return this.live;
  }

  setMuted(muted: boolean): void {
    this.vapi?.setMuted(muted);
  }

  /** Ends the call from our side; safe to call twice. */
  async stop(): Promise<void> {
    this.stopped = true;
    const vapi = this.vapi;
    this.vapi = null;
    if (!vapi) return;
    try {
      await vapi.stop();
    } catch {
      /* the room is already gone */
    }
    vapi.removeAllListeners();
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
