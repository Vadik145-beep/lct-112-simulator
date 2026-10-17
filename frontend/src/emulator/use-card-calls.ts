import { useCallback, useEffect, useRef, useState } from "react";

import { useDial } from "@/api/phones";
import {
  useAnswerServiceCall,
  useEndServiceCall,
  useSayToOfficer,
  useServiceCloudCall,
  useSpeakToOfficer,
  useStartServiceCall,
  type AttemptOut,
} from "@/api/training";
import { CloudCall } from "@/softphone/cloud-call";
import { useSoftphone } from "@/softphone/context";

export type ServiceCall = AttemptOut["service_calls"][number];
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
  const [cloudState, setCloudState] = useState<CloudState>("off");
  const [cloudMuted, setCloudMuted] = useState(false);
  const finished = attempt.state === "finished" || attempt.state === "evaluated";

  const toggleCloudMute = () => {
    const next = !cloudMuted;
    cloudCall.current?.setMuted(next);
    setCloudMuted(next);
  };
  const stopCloudCall = useCallback(async () => {
    const call = cloudCall.current;
    cloudCall.current = null;
    setCloudState("off");
    setCloudMuted(false);
    if (call) await call.stop();
  }, []);
  const startCloudCall = useCallback(
    async (callId: string) => {
      if (cloudCall.current) return;
      setCloudState("connecting");
      setCloudMuted(false);
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
      call
        .on("started", () => setCloudState("live"))
        .on("failed", () => {
          if (cloudCall.current !== call) return;
          void call.stop();
          cloudCall.current = null;
          setCloudState("failed");
          answerCall.mutate(callId);
        })
        .on("ended", () => {
          if (cloudCall.current !== call) return;
          cloudCall.current = null;
          setCloudState("off");
        });
      try {
        await call.start(keys);
      } catch {
        /* о причине сообщит событие failed */
      }
    },
    [cloudKeys, answerCall],
  );

  const [sttAvailable, setSttAvailable] = useState(false);
  const openCall = attempt.service_calls.find((c) => c.ended_at === null) ?? null;
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
    if (cloudLesson && !data.call.telephony) void startCloudCall(data.call.id);
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
    toggleCloudMute,
    sttAvailable,
    openCall,
    shownCall,
    showCall,
    phoneOpen,
    setPhoneOpen,
    busy,
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
