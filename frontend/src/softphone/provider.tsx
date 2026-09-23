import { useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { errorMessage } from "@/api/client";
import { getAccessToken } from "@/api/token";
import { RequestError } from "@/api/training";
import {
  telephonyApi,
  useCurrentCall,
  useSipAccount,
  type DialogOut,
  type TurnResponse,
} from "@/api/telephony";
import { useAuth } from "@/app/use-auth";
import { newActionId } from "@/emulator/draft";
import {
  SoftphoneContext,
  type CallStatus,
  type Softphone,
  type SoftphoneState,
} from "@/softphone/context";
import { CloudCall } from "@/softphone/cloud-call";
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
  serviceCallId: null,
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
  cloud: "off",
  muted: false,
  callerSpeaking: false,
};

function callerLineFromDialog(dialog: DialogOut) {
  const last = [...dialog.turns].reverse().find((t) => t.role === "caller");
  const heard = [...dialog.turns]
    .reverse()
    .find((t) => t.role === "operator" && t.heard);
  if (!last) return null;
  return {
    text: last.text,
    audioUrl: last.audio_url ?? null,
    heardText: heard?.text ?? null,
  };
}

/**
 * Keeps the trainee's softphone alive across pages (registration at sign-in, plan wave 6).
 * In SIP mode the call comes from Asterisk; in browser mode the panel polls the current
 * call-intake attempt and talks through the microphone and /utterance — or, in a lesson
 * with the cloud caller (plan/track-c-vapi.md), through a live WebRTC call to Vapi.
 */
export function SoftphoneProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const isStudent = user?.role === "student";
  const account = useSipAccount(isStudent);
  const sipMode = Boolean(account.data?.enabled);
  const [state, setState] = useState<SoftphoneState>(initialState);
  const phone = useRef<SipPhone | null>(null);
  const cloud = useRef<CloudCall | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const localStream = useRef<MediaStream | null>(null);
  const stopLevel = useRef<(() => void) | null>(null);
  const replyAudio = useRef<HTMLAudioElement | null>(null);
  const client = useQueryClient();
  const patch = useCallback(
    (part: Partial<SoftphoneState>) => setState((s) => ({ ...s, ...part })),
    [],
  );

  const inCall =
    state.status === "incoming" ||
    state.status === "talking" ||
    Boolean(state.serviceCallId);
  // Browser mode: a new attempt shows up by polling; SIP mode: the INVITE tells us, the
  // poll only fills in the attempt when the header is missing.
  const currentCall = useCurrentCall(
    isStudent && !inCall && account.isFetched,
    sipMode ? 5000 : 3000,
  );

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
    void fetch(audioUrl, {
      headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
    })
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => {
        if (!blob) return;
        if (el.src.startsWith("blob:")) URL.revokeObjectURL(el.src);
        el.src = URL.createObjectURL(blob);
        return el.play();
      })
      .catch(() => {});
  }, []);

  // ---------- The cloud caller in the browser (plan/track-c-vapi.md).
  const stopCloud = useCallback(async () => {
    const call = cloud.current;
    cloud.current = null;
    if (!call) return;
    await call.stop();
    setState((s) => ({
      ...s,
      cloud: s.cloud === "failed" ? "failed" : "off",
      muted: false,
      callerSpeaking: false,
      micLevel: 0,
    }));
  }, []);

  const failCloud = useCallback(
    (attemptId: string, reason: string) => {
      cloud.current = null;
      setState((s) => ({
        ...s,
        cloud: "failed",
        muted: false,
        callerSpeaking: false,
        micLevel: 0,
        error: `Облачный заявитель недоступен (${reason}): отвечайте через микрофон, заявитель ответит из утверждённых реплик.`,
      }));
      // Приветствие бэкенд пишет только сейчас (в облаке его говорит Vapi): играем его.
      void telephonyApi
        .cloudCallFailed(attemptId, reason)
        .then((opening) => {
          if (!opening) return;
          setState((s) => ({
            ...s,
            lastCaller: {
              text: opening.text,
              audioUrl: opening.audio_url ?? null,
              heardText: null,
            },
          }));
          playReply(opening.audio_url ?? null);
        })
        .catch(() => null);
    },
    [playReply],
  );

  const startCloud = useCallback(
    async (attemptId: string) => {
      if (cloud.current) return;
      patch({ cloud: "connecting", error: null });
      let keys;
      try {
        keys = await telephonyApi.cloudCall(attemptId);
      } catch (err) {
        // 503: the backend noted the fallback already; the panel goes on locally.
        setState((s) => ({
          ...s,
          cloud: "failed",
          error: errorMessage(
            err,
            err instanceof Error ? err.message : undefined,
          ),
        }));
        return;
      }
      const call = new CloudCall();
      cloud.current = call;
      call
        .on("started", () => patch({ cloud: "live", error: null }))
        .on("speaking", (callerSpeaking) => patch({ callerSpeaking }))
        .on("level", (micLevel) => patch({ micLevel }))
        .on("failed", (reason) => {
          if (cloud.current !== call) return;
          void call.stop();
          failCloud(attemptId, reason);
        })
        .on("ended", () => {
          if (cloud.current !== call) return;
          cloud.current = null;
          patch({
            cloud: "off",
            muted: false,
            callerSpeaking: false,
            micLevel: 0,
          });
          // The caller hung up or the call dropped: the poll picks the server's verdict.
          void client.invalidateQueries({ queryKey: ["dialog", attemptId] });
        });
      try {
        await call.start(keys);
      } catch {
        /* reported through "failed" */
      }
    },
    [patch, failCloud, client],
  );

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
        status:
          s.status === "incoming" || s.status === "talking"
            ? s.status
            : registration === "ready"
              ? "ready"
              : "disconnected",
      })),
    );
    sip.on("incoming", (call) => {
      if (call.serviceCallId) {
        // The dispatcher started this call from the card: pick up at once, the card page
        // shows the conversation (issue #36).
        setState((s) => ({
          ...s,
          status: "incoming",
          serviceCallId: call.serviceCallId,
          attemptId: call.attemptId ?? s.attemptId,
          callerNumber: call.callerNumber,
          callerName: call.callerName,
          endReason: null,
          error: null,
          lastCaller: null,
          stats: null,
        }));
        sip.answer();
        return;
      }
      setState((s) => ({
        ...s,
        status: "incoming",
        serviceCallId: null,
        attemptId: call.attemptId ?? s.attemptId,
        callerNumber: call.callerNumber,
        callerName: call.callerName,
        endReason: null,
        error: null,
        lastCaller: null,
        stats: null,
      }));
    });
    sip.on("talking", () => patch({ status: "talking" }));
    sip.on("level", (micLevel) => patch({ micLevel }));
    sip.on("stats", (stats: CallStats) => patch({ stats }));
    sip.on("ended", () =>
      setState((s) => ({
        ...s,
        // An officer's call leaves the phone ready again; a 112 call shows «завершён».
        status:
          s.status === "disconnected"
            ? "disconnected"
            : s.serviceCallId
              ? "ready"
              : "ended",
        serviceCallId: null,
        micLevel: 0,
        recording: false,
      })),
    );
    const wsUrl = `wss://${location.host}${account.data.ws_path}`;
    sip.register(
      wsUrl,
      `sip:${account.data.username}@${account.data.domain}`,
      account.data.password,
      account.data.display_name,
    );
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
        const ringing =
          call.call_state === "idle" || call.call_state === "ringing";
        return {
          ...s,
          attemptId: call.attempt_id,
          sessionId: call.session_id,
          callerNumber: call.caller_number,
          scenarioTitle: call.scenario_title,
          status: ringing
            ? "incoming"
            : call.call_state === "answered"
              ? "talking"
              : s.status,
          endReason: null,
        };
      }
      return {
        ...s,
        attemptId: s.attemptId ?? call.attempt_id,
        sessionId: call.session_id,
        scenarioTitle: call.scenario_title,
      };
    });
  }, [currentCall.data]);

  // ---------- Transcript while talking (a 112 call; the card page follows an officer's call).
  useEffect(() => {
    if (state.status !== "talking" || !state.attemptId || state.serviceCallId)
      return;
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
          ...(dialog.call.state === "ended"
            ? {
                status: "ended" as CallStatus,
                endReason: dialog.call.end_reason ?? null,
              }
            : {}),
        }));
        if (dialog.call.state === "ended") {
          phone.current?.hangup();
          void stopCloud();
        }
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
  }, [state.status, state.attemptId, state.serviceCallId, stopCloud]);

  useEffect(() => {
    void refreshDevices();
  }, [refreshDevices]);

  const attemptOrThrow = () => {
    if (!state.attemptId)
      throw new Error("Вызов не привязан к карточке: обновите страницу.");
    return state.attemptId;
  };

  const withBusy = useCallback(
    async (work: () => Promise<void>) => {
      patch({ busy: true, error: null });
      try {
        await work();
      } catch (err) {
        patch({
          error: errorMessage(
            err,
            err instanceof Error ? err.message : undefined,
          ),
        });
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
        lastCaller: {
          text: turn.caller.text,
          audioUrl: turn.caller.audio_url ?? null,
          heardText: turn.heard_text ?? null,
        },
        sttAvailable: turn.dialog.stt_available,
        ...(turn.call_ended
          ? {
              status: "ended" as CallStatus,
              endReason: turn.dialog.call.end_reason ?? null,
            }
          : {}),
      }));
      playReply(turn.caller.audio_url ?? null);
      void client.invalidateQueries({
        queryKey: ["dialog", turn.dialog.attempt_id],
      });
      if (turn.call_ended) stopLocalStream();
    },
    [client, playReply, stopLocalStream],
  );

  const answer = useCallback(
    () =>
      withBusy(async () => {
        if (state.mode === "sip") {
          phone.current?.answer();
          if (state.attemptId)
            await telephonyApi.answer(state.attemptId).catch(() => null);
          return;
        }
        const attemptId = attemptOrThrow();
        const result = await telephonyApi.answer(attemptId);
        const cloudLesson = result.dialog.mode === "cloud";
        setState((s) => ({
          ...s,
          status: "talking",
          sessionId:
            result.dialog.seq !== undefined ? s.sessionId : s.sessionId,
          // В облачном занятии своей реплики приветствия нет: его говорит Vapi.
          lastCaller: result.opening
            ? {
                text: result.opening.text,
                audioUrl: result.opening.audio_url ?? null,
                heardText: null,
              }
            : null,
          sttAvailable: result.dialog.stt_available,
          cloud: cloudLesson ? "connecting" : "off",
        }));
        void client.invalidateQueries({ queryKey: ["dialog", attemptId] });
        if (cloudLesson) {
          // Приветствие говорит сам облачный заявитель; запасной путь получает свою
          // реплику от бэкенда (POST …/cloud-call/failed) и играет её оттуда.
          await startCloud(attemptId);
        } else {
          playReply(result.opening?.audio_url ?? null);
        }
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [withBusy, state.mode, state.attemptId, playReply, client, startCloud],
  );

  const finish = useCallback(
    (
      call: (attemptId: string) => Promise<{ dialog: DialogOut }>,
      reason: string,
    ) =>
      withBusy(async () => {
        phone.current?.hangup();
        stopLocalStream();
        await stopCloud();
        const attemptId = state.attemptId;
        if (attemptId) {
          try {
            const result = await call(attemptId);
            patch({
              status: "ended",
              endReason: result.dialog.call.end_reason ?? reason,
            });
          } catch (error) {
            // The card was saved (or closed by the teacher) while the panel still showed
            // «разговор»: the call is over on the server, so the panel ends too.
            if (
              error instanceof RequestError &&
              error.code === "attempt_closed"
            ) {
              patch({ status: "ended", endReason: reason });
            } else throw error;
          }
        } else {
          patch({ status: "ended", endReason: reason });
        }
        void client.invalidateQueries({ queryKey: ["me", "call"] });
      }),
    [withBusy, state.attemptId, patch, stopLocalStream, stopCloud, client],
  );

  const hangup = useCallback(
    () => finish(telephonyApi.hangup, "hangup"),
    [finish],
  );
  const noContact = useCallback(
    () => finish(telephonyApi.noContact, "no_contact"),
    [finish],
  );
  const callDropped = useCallback(
    () => finish(telephonyApi.callDropped, "call_dropped"),
    [finish],
  );

  const setMicDevice = useCallback(
    (deviceId: string | null) => {
      storeMicDevice(deviceId);
      if (phone.current) phone.current.micDeviceId = deviceId;
      patch({ micDeviceId: deviceId });
    },
    [patch],
  );

  const dismiss = useCallback(() => {
    void stopCloud();
    setState((s) => ({
      ...s,
      status: s.registration === "ready" ? "ready" : "disconnected",
      attemptId: null,
      lastCaller: null,
      endReason: null,
      stats: null,
      error: null,
      cloud: "off",
      muted: false,
      callerSpeaking: false,
    }));
    void client.invalidateQueries({ queryKey: ["me", "call"] });
  }, [client, stopCloud]);

  const setMuted = useCallback(
    (muted: boolean) => {
      cloud.current?.setMuted(muted);
      patch({ muted });
    },
    [patch],
  );

  const startRecording = useCallback(async () => {
    if (
      state.mode !== "browser" ||
      state.status !== "talking" ||
      state.cloud === "live" ||
      state.cloud === "connecting" ||
      recorder.current
    )
      return;
    try {
      const constraints: MediaStreamConstraints = {
        audio: state.micDeviceId
          ? { deviceId: { exact: state.micDeviceId } }
          : true,
      };
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      localStream.current = stream;
      stopLevel.current = watchLevel(stream, (micLevel) => patch({ micLevel }));
      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: "audio/webm;codecs=opus",
      });
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
          const turn = await telephonyApi.utterance(
            attemptId,
            blob,
            newActionId(),
          );
          applyTurn(turn);
        });
      };
      recorder.current = mediaRecorder;
      mediaRecorder.start();
      patch({ recording: true, error: null });
    } catch (err) {
      patch({
        error:
          err instanceof Error && err.name === "NotAllowedError"
            ? "Нет доступа к микрофону: разрешите его в браузере."
            : errorMessage(err),
      });
    }
  }, [
    state.mode,
    state.status,
    state.cloud,
    state.micDeviceId,
    state.attemptId,
    patch,
    withBusy,
    applyTurn,
    stopLocalStream,
  ]);

  const stopRecording = useCallback(async () => {
    const mediaRecorder = recorder.current;
    if (mediaRecorder && mediaRecorder.state !== "inactive")
      mediaRecorder.stop();
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
            setMuted,
          }
        : null,
    [
      isStudent,
      state,
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
      setMuted,
    ],
  );

  return (
    <SoftphoneContext.Provider value={value}>
      {children}
    </SoftphoneContext.Provider>
  );
}
