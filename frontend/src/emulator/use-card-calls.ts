import { useCallback, useEffect, useRef, useState } from "react";

import { useDial } from "@/api/phones";
import {
  endServiceCallOf,
  useAnswerServiceCall,
  useEndServiceCall,
  useSayToOfficer,
  useServiceCloudCall,
  useSpeakToOfficer,
  useStartServiceCall,
  type AttemptOut,
} from "@/api/training";
import { RINGBACK_MS, playRingback } from "@/emulator/phone-tones";
import { CloudCall, MIC_ASK_HINT } from "@/softphone/cloud-call";
import { useSoftphone } from "@/softphone/context";

export type ServiceCall = AttemptOut["service_calls"][number];
// The browser asks for the microphone this long before the phone points at its question.
const ASK_HINT_MS = 1500;

export type CloudState = "off" | "connecting" | "live" | "failed";

/**
 * The calls of a card in one place (issue #36, #103, #263): one call open at a time; the
 * softphone carries the voice when telephony is on, a cloud lesson talks from the browser,
 * otherwise the conversation goes in text. The handsets of the card and the virtual phone
 * share it, so a call started anywhere shows in the one window of the phone.
 */
export function useCardCalls(attempt: AttemptOut) {
  const softphone = useSoftphone();
  const startCall = useStartServiceCall(attempt.id);
  const dialCall = useDial(attempt.id);
  const sayToOfficer = useSayToOfficer(attempt.id);
  const speakToOfficer = useSpeakToOfficer(attempt.id);
  const endCall = useEndServiceCall(attempt.id);
  const answerCall = useAnswerServiceCall(attempt.id);
  const cloudKeys = useServiceCloudCall(attempt.id);
  // Занятие с облачным голосом: собеседников играет облако прямо из браузера.
  const cloudLesson = attempt.session.dialog_mode === "cloud";
  const cloudCall = useRef<CloudCall | null>(null);
  // The record of the card the cloud call carries: the voice lives exactly as long as it.
  const cloudCallId = useRef<string | null>(null);
  const [cloudState, setCloudState] = useState<CloudState>("off");
  const [cloudMuted, setCloudMuted] = useState(false);
  // Why the cloud call did not start or dropped, in plain words; a hint while it is quiet.
  const [cloudError, setCloudError] = useState<string | null>(null);
  const [cloudWarning, setCloudWarning] = useState<string | null>(null);
  const [cloudLevel, setCloudLevel] = useState(0);
  const askTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Another microphone chosen in the phone during a cloud call goes into it at once.
  const micDeviceId = softphone?.micDeviceId ?? null;
  const callMic = useRef(micDeviceId);
  useEffect(() => {
    if (callMic.current === micDeviceId) return;
    callMic.current = micDeviceId;
    const call = cloudCall.current;
    if (call?.isLive) {
      setCloudWarning(null);
      void call.switchMicrophone(micDeviceId);
    }
  }, [micDeviceId]);
  useEffect(() => () => {
    if (askTimer.current) clearTimeout(askTimer.current);
  }, []);
  const finished = attempt.state === "finished" || attempt.state === "evaluated";

  const toggleCloudMute = () => {
    const next = !cloudMuted;
    cloudCall.current?.setMuted(next);
    setCloudMuted(next);
  };
  const stopCloudCall = useCallback(async () => {
    const call = cloudCall.current;
    cloudCall.current = null;
    cloudCallId.current = null;
    setCloudState("off");
    setCloudMuted(false);
    if (call) await call.stop();
  }, []);
  const startCloudCall = useCallback(
    async (callId: string) => {
      if (cloudCall.current) return;
      setCloudState("connecting");
      setCloudMuted(false);
      setCloudError(null);
      setCloudWarning(null);
      setCloudLevel(0);
      let keys;
      try {
        keys = await cloudKeys.mutateAsync(callId);
      } catch {
        // Облака нет: звонок не оставляем неотвеченным, дальше разговор текстом.
        setCloudState("failed");
        answerCall.mutate(callId);
        return;
      }
      const call = new CloudCall();
      cloudCall.current = call;
      cloudCallId.current = callId;
      call
        .on("started", () => {
          setCloudState("live");
          // The microphone is open now: the list of microphones has their names.
          void softphone?.refreshDevices();
        })
        .on("level", (level) => {
          setCloudLevel(level);
          if (level >= 0.02) setCloudWarning(null);
        })
        .on("warning", (hint) => setCloudWarning(hint))
        .on("asking", (asking) => {
          // The browser's question is easy to miss: after a moment the phone says where it is.
          if (askTimer.current) clearTimeout(askTimer.current);
          askTimer.current = null;
          if (asking) askTimer.current = setTimeout(() => setCloudWarning(MIC_ASK_HINT), ASK_HINT_MS);
          else setCloudWarning((w) => (w === MIC_ASK_HINT ? null : w));
        })
        .on("failed", (reason) => {
          if (cloudCall.current !== call) return;
          void call.stop();
          cloudCall.current = null;
          cloudCallId.current = null;
          setCloudState("failed");
          setCloudError(reason);
          setCloudWarning(null);
          answerCall.mutate(callId);
        })
        .on("ended", () => {
          if (cloudCall.current !== call) return;
          cloudCall.current = null;
          cloudCallId.current = null;
          setCloudState("off");
        });
      try {
        await call.start(keys, softphone?.micDeviceId ?? null);
      } catch {
        /* о причине сообщит событие failed */
      }
    },
    [cloudKeys, answerCall, softphone],
  );

  const [sttAvailable, setSttAvailable] = useState(false);
  const openCall = attempt.service_calls.find((c) => c.ended_at === null) ?? null;
  // A call made from the browser beeps before the other side answers (#270): the answer the
  // server already wrote shows when the beeps end; a cloud call beeps twice as well and stops
  // beeping as soon as its voice is on (a slower one connects in silence).
  // A call over the softphone has the beeps of the telephone line.
  const [dialingId, setDialingId] = useState<string | null>(null);
  const stopBeeps = useRef<(() => void) | null>(null);
  const endBeeps = useCallback(() => {
    stopBeeps.current?.();
    stopBeeps.current = null;
    setDialingId(null);
  }, []);
  const beep = (callId: string) => {
    endBeeps();
    setDialingId(callId);
    stopBeeps.current = playRingback(RINGBACK_MS);
  };
  useEffect(() => {
    if (!dialingId || cloudLesson) return;
    const timer = setTimeout(endBeeps, RINGBACK_MS);
    return () => clearTimeout(timer);
  }, [dialingId, cloudLesson, endBeeps]);
  useEffect(() => {
    if (dialingId && cloudLesson && cloudState !== "connecting") endBeeps();
  }, [dialingId, cloudLesson, cloudState, endBeeps]);
  // The call ended or another one is on the line: no more beeps. The new call itself may come
  // into the card a render later than the dial answers.
  const openId = openCall?.id ?? null;
  const dialingOver =
    (openId !== null && openId !== dialingId) ||
    attempt.service_calls.some((c) => c.id === dialingId && c.ended_at !== null);
  useEffect(() => {
    if (dialingId && dialingOver) endBeeps();
  }, [dialingId, dialingOver, endBeeps]);
  useEffect(() => () => stopBeeps.current?.(), []);
  const [shownCallId, setShownCallId] = useState<string | null>(null);
  // The window of the virtual phone: a call started or ringing opens it.
  const [phoneOpen, setPhoneOpen] = useState(false);
  // The call on the line stays shown when it ends; a line of the history opens an earlier one.
  const openCallId = openCall?.id ?? null;
  useEffect(() => {
    if (!openCallId) return;
    setShownCallId(openCallId);
    setPhoneOpen(true);
  }, [openCallId]);
  // The server closed the call (a report not taken in time, the officer hung up): the voice
  // goes down with it, or it would hold the line and no other call would connect (stand
  // 18.10.2026: after one card the calls of the others stayed «Соединение со службой…»).
  useEffect(() => {
    if (cloudCall.current && cloudCallId.current !== openCallId) void stopCloudCall();
  }, [openCallId, stopCloudCall]);
  // The page shows another card or closes: the call of the card left ends, voice and record.
  useEffect(() => {
    const attemptId = attempt.id;
    return () => {
      const call = cloudCall.current;
      const callId = cloudCallId.current;
      if (!call) return;
      cloudCall.current = null;
      cloudCallId.current = null;
      void call.stop();
      if (callId) void endServiceCallOf(attemptId, callId);
    };
  }, [attempt.id]);
  // A closed card has nothing to call about: the phone folds and leaves the result in view.
  useEffect(() => {
    if (finished) setPhoneOpen(false);
  }, [finished]);
  /** Shows a call of the card in the phone (``null`` — back to the keypad). */
  const showCall = (id: string | null) => {
    setShownCallId(id);
    if (id) setPhoneOpen(true);
  };
  const shownCall =
    openCall ?? attempt.service_calls.find((c) => c.id === shownCallId) ?? null;

  const busy =
    startCall.isPending ||
    dialCall.isPending ||
    sayToOfficer.isPending ||
    speakToOfficer.isPending ||
    answerCall.isPending ||
    endCall.isPending;
  const error =
    startCall.error ??
    sayToOfficer.error ??
    speakToOfficer.error ??
    answerCall.error ??
    endCall.error;
  const canCall = !finished && !openCall && !busy;

  const started = (data: { call: ServiceCall; stt_available: boolean }) => {
    setShownCallId(data.call.id);
    setSttAvailable(data.stt_available);
    if (data.call.telephony) return;
    beep(data.call.id);
    if (cloudLesson) void startCloudCall(data.call.id);
  };
  /** A handset of the card: a service of the strip or the caller (``CALLER_TARGET``). */
  const call = (service: string) => {
    if (!canCall) return;
    startCall.mutate(service, { onSuccess: started });
  };
  /** A number dialled on the virtual phone: the server tells who answers. */
  const dial = (number: string) => {
    if (!canCall) return;
    dialCall.mutate(number, { onSuccess: started });
  };
  // The squad's report is an incoming call (issue #103): the trainee answers it. The phone
  // carries the voice when it is the one ringing; otherwise the card answers over the API.
  const answer = () => {
    if (!openCall || busy) return;
    if (softphone?.serviceCallId === openCall.id) {
      void softphone.answer();
      return;
    }
    if (cloudLesson && !openCall.telephony) {
      void startCloudCall(openCall.id);
      return;
    }
    answerCall.mutate(openCall.id, {
      onSuccess: (data) => setSttAvailable(data.stt_available),
    });
  };
  const hangUp = () => {
    if (!openCall) return;
    endBeeps();
    void stopCloudCall();
    endCall.mutate(openCall.id, {
      onSuccess: () => {
        // The SIP leg of the officer's call goes down with the record.
        if (softphone?.serviceCallId) void softphone.hangup();
      },
    });
  };
  const say = (callId: string, text: string, actionId: string) =>
    sayToOfficer.mutate({ callId, text, actionId });
  const speak = (callId: string, blob: Blob, actionId: string) =>
    speakToOfficer.mutate({ callId, blob, actionId });

  return {
    softphone,
    finished,
    cloudLesson,
    cloudState,
    cloudMuted,
    cloudError,
    cloudWarning,
    cloudLevel,
    toggleCloudMute,
    sttAvailable,
    openCall,
    shownCall,
    showCall,
    phoneOpen,
    setPhoneOpen,
    busy,
    dialing: dialingId !== null && dialingId === openId,
    replying: sayToOfficer.isPending || speakToOfficer.isPending,
    error,
    dialError: dialCall.error,
    resetDialError: dialCall.reset,
    canCall,
    call,
    dial,
    answer,
    hangUp,
    say,
    speak,
  };
}

export type CardCalls = ReturnType<typeof useCardCalls>;
