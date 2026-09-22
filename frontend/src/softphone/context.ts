import { createContext, useContext } from "react";

import type { CallStats, RegistrationState } from "@/softphone/sip-phone";

/** Panel states (plan wave 6): не подключён → готов → входящий → разговор → завершён. */
export type CallStatus =
  "disconnected" | "ready" | "incoming" | "talking" | "ended";

export interface CallerLine {
  text: string;
  audioUrl: string | null;
  heardText: string | null; // what the recognizer understood from the operator
}

export interface SoftphoneState {
  /** "sip" — Asterisk and WebRTC; "browser" — microphone and /utterance (TELEPHONY_ENABLED=false). */
  mode: "sip" | "browser";
  status: CallStatus;
  registration: RegistrationState;
  registrationDetail: string | null;
  attemptId: string | null;
  sessionId: string | null;
  /** Set while the phone carries a call to a service officer (issue #36); the card shows it. */
  serviceCallId: string | null;
  callerNumber: string;
  callerName: string;
  scenarioTitle: string;
  micLevel: number;
  stats: CallStats | null;
  devices: MediaDeviceInfo[];
  micDeviceId: string | null;
  lastCaller: CallerLine | null;
  sttAvailable: boolean;
  endReason: string | null;
  busy: boolean;
  error: string | null;
  /** Browser mode: the phrase is being recorded. */
  recording: boolean;
  /**
   * Browser mode of a lesson with the cloud caller (plan/track-c-vapi.md): the call to Vapi
   * is being set up, is live (the microphone is open, the caller answers by itself), or
   * failed (the panel fell back to the microphone button and the local stand-by).
   */
  cloud: "off" | "connecting" | "live" | "failed";
  /** Cloud call: the microphone is muted. */
  muted: boolean;
  /** Cloud call: the caller is speaking right now. */
  callerSpeaking: boolean;
}

export interface SoftphoneActions {
  answer: () => Promise<void>;
  hangup: () => Promise<void>;
  noContact: () => Promise<void>;
  callDropped: () => Promise<void>;
  setMicDevice: (deviceId: string | null) => void;
  refreshDevices: () => Promise<void>;
  dismiss: () => void;
  /** Browser mode: press to talk, release to send. */
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<void>;
  sayText: (text: string) => Promise<void>;
  /** Cloud call: mute or unmute the microphone. */
  setMuted: (muted: boolean) => void;
}

export type Softphone = SoftphoneState & SoftphoneActions;

export const SoftphoneContext = createContext<Softphone | null>(null);

export function useSoftphone(): Softphone | null {
  return useContext(SoftphoneContext);
}

export const STATUS_LABELS: Record<CallStatus, string> = {
  disconnected: "не подключён",
  ready: "готов",
  incoming: "входящий",
  talking: "разговор",
  ended: "завершён",
};

export const END_REASON_LABELS: Record<string, string> = {
  hangup: "вы завершили вызов",
  caller_hangup: "заявитель положил трубку",
  silence: "связь прервана: долгая тишина",
  no_answer: "не ответили вовремя",
  no_contact: "отмечено «нет контакта»",
  call_dropped: "отмечено «срыв звонка»",
  failed: "звонок не удался",
  card_saved: "карточка сохранена",
};
