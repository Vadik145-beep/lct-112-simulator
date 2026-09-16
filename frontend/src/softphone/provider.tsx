import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { errorMessage } from "@/api/client";
import { getAccessToken } from "@/api/token";
import { telephonyApi, useCurrentCall, useSipAccount, type DialogOut, type TurnResponse } from "@/api/telephony";
import { useAuth } from "@/app/use-auth";
import { newActionId } from "@/emulator/draft";
import { SoftphoneContext, type CallStatus, type Softphone, type SoftphoneState } from "@/softphone/context";
import {
  SipPhone,
  storeMicDevice,
  storedMicDevice,
  watchLevel,
  type CallStats,
  type RegistrationState,
} from "@/softphone/sip-phone";

// While talking the transcript is refreshed so the panel shows the caller's last words.
const DIALOG_POLL_MS = 2000;

const initialState: SoftphoneState = {
  mode: "browser",
  status: "disconnected",
  registration: "disconnected",
  registrationDetail: null,
  attemptId: null,
  sessionId: null,
  callerNumber: "",
  callerName: "",
  scenarioTitle: "",
  micLevel: 0,
  stats: null,
  devices: [],
  micDeviceId: storedMicDevice(),
  lastCaller: null,
  sttAvailable: true,
  endReason: null,
  busy: false,
  error: null,
  recording: false,
};

function callerLineFromDialog(dialog: DialogOut) {
  const last = [...dialog.turns].reverse().find((t) => t.role === "caller");
  const heard = [...dialog.turns].reverse().find((t) => t.role === "operator" && t.heard);
  if (!last) return null;
  return { text: last.text, audioUrl: last.audio_url ?? null, heardText: heard?.text ?? null };
}

/**
 * Keeps the trainee's softphone alive across pages (registration at sign-in, plan wave 6).
 * In SIP mode the call comes from Asterisk; in browser mode the panel polls the current
 * call-intake attempt and talks through the microphone and /utterance.
 */
export function SoftphoneProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const isStudent = user?.role === "student";
  const account = useSipAccount(isStudent);
  const sipMode = Boolean(account.data?.enabled);
  const [state, setState] = useState<SoftphoneState>(initialState);
  const phone = useRef<SipPhone | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const localStream = useRef<MediaStream | null>(null);
  const stopLevel = useRef<(() => void) | null>(null);
  const replyAudio = useRef<HTMLAudioElement | null>(null);
  const client = useQueryClient();
  const patch = useCallback((part: Partial<SoftphoneState>) => setState((s) => ({ ...s, ...part })), []);

  const inCall = state.status === "incoming" || state.status === "talking";
  // Browser mode: a new attempt shows up by polling; SIP mode: the INVITE tells us, the
  // poll only fills in the attempt when the header is missing.
  const currentCall = useCurrentCall(isStudent && !inCall && account.isFetched, sipMode ? 5000 : 3000);

  const refreshDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const all = await navigator.mediaDevices.enumerateDevices();
    patch({ devices: all.filter((d) => d.kind === "audioinput") });
  }, [patch]);

  // Media files need the bearer token, which an Audio src cannot carry: fetch → blob.
  const playReply = useCallback((audioUrl: string | null) => {
    if (!audioUrl) return;
    if (!replyAudio.current) replyAudio.current = new Audio();
    const el = replyAudio.current;
    void fetch(audioUrl, { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` } })
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => {
        if (!blob) return;
        if (el.src.startsWith("blob:")) URL.revokeObjectURL(el.src);
        el.src = URL.createObjectURL(blob);
        return el.play();
      })
      .catch(() => {});
  }, []);

  // ---------- SIP mode: register once the credentials are known.
  useEffect(() => {
    if (!isStudent || !sipMode || !account.data) return;
    const sip = new SipPhone();
    sip.micDeviceId = storedMicDevice();
    phone.current = sip;
    patch({ mode: "sip", registration: "connecting", status: "disconnected" });
    sip.on("registration", (registration: RegistrationState, detail?: string) =>
      setState((s) => ({
        ...s,
        registration,
        registrationDetail: detail ?? null,
        status: s.status === "incoming" || s.status === "talking" ? s.status : registration === "ready" ? "ready" : "disconnected",
      })),
    );
    sip.on("incoming", (call) =>
      setState((s) => ({
        ...s,
        status: "incoming",
        attemptId: call.attemptId ?? s.attemptId,
        callerNumber: call.callerNumber,
        callerName: call.callerName,
        endReason: null,
        error: null,
        lastCaller: null,
        stats: null,
      })),
    );
    sip.on("talking", () => patch({ status: "talking" }));
    sip.on("level", (micLevel) => patch({ micLevel }));
    sip.on("stats", (stats: CallStats) => patch({ stats }));
    sip.on("ended", () =>
      setState((s) => ({
        ...s,
        status: s.status === "disconnected" ? "disconnected" : "ended",
        micLevel: 0,
        recording: false,
      })),
    );
    const wsUrl = `wss://${location.host}${account.data.ws_path}`;
    sip.register(wsUrl, `sip:${account.data.username}@${account.data.domain}`, account.data.password, account.data.display_name);
    return () => {
      sip.destroy();
      phone.current = null;
    };
  }, [isStudent, sipMode, account.data, patch]);

  // ---------- Browser mode: ready as soon as the account says telephony is off.
  useEffect(() => {
    if (!isStudent || !account.isFetched || sipMode) return;
    patch({ mode: "browser", registration: "ready", status: "ready" });
  }, [isStudent, account.isFetched, sipMode, patch]);

  // ---------- A ringing / open attempt from the poll.
  useEffect(() => {
    const call = currentCall.data;
    if (!call) return;
    setState((s) => {
      if (s.status === "talking") return s;
      if (s.mode === "browser") {
        const ringing = call.call_state === "idle" || call.call_state === "ringing";
        return {
          ...s,
          attemptId: call.attempt_id,
          sessionId: call.session_id,
          callerNumber: call.caller_number,
          scenarioTitle: call.scenario_title,
          status: ringing ? "incoming" : call.call_state === "answered" ? "talking" : s.status,
          endReason: null,
        };
      }
      return { ...s, attemptId: s.attemptId ?? call.attempt_id, sessionId: call.session_id, scenarioTitle: call.scenario_title };
    });
  }, [currentCall.data]);

  // ---------- Transcript while talking.
  useEffect(() => {
    if (state.status !== "talking" || !state.attemptId) return;
    const attemptId = state.attemptId;
    let stopped = false;
    const tick = async () => {
      try {
        const dialog = await telephonyApi.dialog(attemptId);
        if (stopped) return;
        const line = callerLineFromDialog(dialog);
        setState((s) => ({
          ...s,
          lastCaller: line ?? s.lastCaller,
          sttAvailable: dialog.stt_available,
          ...(dialog.call.state === "ended" ? { status: "ended" as CallStatus, endReason: dialog.call.end_reason ?? null } : {}),
        }));
        if (dialog.call.state === "ended") phone.current?.hangup();
      } catch {
        /* next tick */
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), DIALOG_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [state.status, state.attemptId]);

  useEffect(() => {
    void refreshDevices();
  }, [refreshDevices]);

  const attemptOrThrow = () => {
    if (!state.attemptId) throw new Error("Вызов не привязан к карточке: обновите страницу.");
    return state.attemptId;
  };

  const withBusy = useCallback(
    async (work: () => Promise<void>) => {
      patch({ busy: true, error: null });
      try {
        await work();
      } catch (err) {
        patch({ error: errorMessage(err, err instanceof Error ? err.message : undefined) });
      } finally {
        patch({ busy: false });
      }
    },
    [patch],
  );

  const stopLocalStream = useCallback(() => {
    stopLevel.current?.();
    stopLevel.current = null;
    localStream.current?.getTracks().forEach((t) => t.stop());
    localStream.current = null;
    patch({ micLevel: 0 });
  }, [patch]);

  const applyTurn = useCallback(
    (turn: TurnResponse) => {
      setState((s) => ({
        ...s,
        lastCaller: { text: turn.caller.text, audioUrl: turn.caller.audio_url ?? null, heardText: turn.heard_text ?? null },
        sttAvailable: turn.dialog.stt_available,
        ...(turn.call_ended ? { status: "ended" as CallStatus, endReason: turn.dialog.call.end_reason ?? null } : {}),
      }));
      playReply(turn.caller.audio_url ?? null);
      void client.invalidateQueries({ queryKey: ["dialog", turn.dialog.attempt_id] });
      if (turn.call_ended) stopLocalStream();
    },
    [client, playReply, stopLocalStream],
  );

  const answer = useCallback(
    () =>
      withBusy(async () => {
        if (state.mode === "sip") {
          phone.current?.answer();
          if (state.attemptId) await telephonyApi.answer(state.attemptId).catch(() => null);
          return;
        }
        const attemptId = attemptOrThrow();
        const result = await telephonyApi.answer(attemptId);
        setState((s) => ({
          ...s,
          status: "talking",
          sessionId: result.dialog.seq !== undefined ? s.sessionId : s.sessionId,
          lastCaller: { text: result.opening.text, audioUrl: result.opening.audio_url ?? null, heardText: null },
          sttAvailable: result.dialog.stt_available,
        }));
        playReply(result.opening.audio_url ?? null);
        void client.invalidateQueries({ queryKey: ["dialog", attemptId] });
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [withBusy, state.mode, state.attemptId, playReply, client],
  );

  const finish = useCallback(
    (call: (attemptId: string) => Promise<{ dialog: DialogOut }>, reason: string) =>
      withBusy(async () => {
        phone.current?.hangup();
        stopLocalStream();
        const attemptId = state.attemptId;
        if (attemptId) {
          const result = await call(attemptId);
          patch({ status: "ended", endReason: result.dialog.call.end_reason ?? reason });
        } else {
          patch({ status: "ended", endReason: reason });
        }
        void client.invalidateQueries({ queryKey: ["me", "call"] });
      }),
    [withBusy, state.attemptId, patch, stopLocalStream, client],
  );

  const hangup = useCallback(() => finish(telephonyApi.hangup, "hangup"), [finish]);
  const noContact = useCallback(() => finish(telephonyApi.noContact, "no_contact"), [finish]);
  const callDropped = useCallback(() => finish(telephonyApi.callDropped, "call_dropped"), [finish]);

  const setMicDevice = useCallback(
    (deviceId: string | null) => {
      storeMicDevice(deviceId);
      if (phone.current) phone.current.micDeviceId = deviceId;
      patch({ micDeviceId: deviceId });
    },
    [patch],
  );

  const dismiss = useCallback(() => {
    setState((s) => ({
      ...s,
      status: s.registration === "ready" ? "ready" : "disconnected",
      attemptId: null,
      lastCaller: null,
      endReason: null,
      stats: null,
      error: null,
    }));
    void client.invalidateQueries({ queryKey: ["me", "call"] });
  }, [client]);

  const startRecording = useCallback(async () => {
    if (state.mode !== "browser" || state.status !== "talking" || recorder.current) return;
    try {
      const constraints: MediaStreamConstraints = {
        audio: state.micDeviceId ? { deviceId: { exact: state.micDeviceId } } : true,
      };
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      localStream.current = stream;
      stopLevel.current = watchLevel(stream, (micLevel) => patch({ micLevel }));
      const mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      const chunks: Blob[] = [];
      mediaRecorder.ondataavailable = (ev) => {
        if (ev.data.size) chunks.push(ev.data);
      };
      mediaRecorder.onstop = () => {
        const blob = new Blob(chunks, { type: "audio/webm" });
        stopLocalStream();
        recorder.current = null;
        patch({ recording: false });
        if (!state.attemptId || blob.size < 1000) return;
        const attemptId = state.attemptId;
        void withBusy(async () => {
          const turn = await telephonyApi.utterance(attemptId, blob, newActionId());
          applyTurn(turn);
        });
      };
      recorder.current = mediaRecorder;
      mediaRecorder.start();
      patch({ recording: true, error: null });
    } catch (err) {
      patch({ error: err instanceof Error && err.name === "NotAllowedError" ? "Нет доступа к микрофону: разрешите его в браузере." : errorMessage(err) });
    }
  }, [state.mode, state.status, state.micDeviceId, state.attemptId, patch, withBusy, applyTurn, stopLocalStream]);

  const stopRecording = useCallback(async () => {
    const mediaRecorder = recorder.current;
    if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  }, []);

  const sayText = useCallback(
    (text: string) =>
      withBusy(async () => {
        const attemptId = attemptOrThrow();
        applyTurn(await telephonyApi.say(attemptId, text, newActionId()));
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [withBusy, state.attemptId, applyTurn],
  );

  const value = useMemo<Softphone | null>(
    () =>
      isStudent
        ? {
            ...state,
            answer,
            hangup,
            noContact,
            callDropped,
            setMicDevice,
            refreshDevices,
            dismiss,
            startRecording,
            stopRecording,
            sayText,
          }
        : null,
    [isStudent, state, answer, hangup, noContact, callDropped, setMicDevice, refreshDevices, dismiss, startRecording, stopRecording, sayText],
  );

  return <SoftphoneContext.Provider value={value}>{children}</SoftphoneContext.Provider>;
}
