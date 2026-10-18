import { useEffect } from "react";

/**
 * Sounds of the virtual phone (#270, decision of 19.10.2026): long beeps before the other side
 * picks up and a short ring of an incoming call. Made in the browser — no files, no network.
 * One sound at a time: a call that starts ringing while a sound plays stays silent (the phone
 * shows it), so two squads calling together never ring over each other or over the beeps.
 */

// The Russian telephone tone; the beeps go 1 s on, 0.5 s off.
const BEEP_HZ = 425;
const BEEP_ON_S = 1;
const BEEP_PERIOD_S = 1.5;
/** How long a call made from the phone rings before the other side answers. */
export const RINGBACK_MS = 2500;
/** A cloud call rings until the voice connects, but not longer than this. */
export const RINGBACK_MAX_MS = 15000;
// The ring of an incoming call: two trills of two notes, 2 s in all, once per call.
const RING_NOTES_HZ = [1320, 1050];
const RING_NOTE_S = 0.06;
const RING_TRILL_S = 0.8;
const RING_GAP_S = 0.4;
const VOLUME = 0.12;
// A remembered call does not ring again when the trainee goes from the journal to the card.
const RUNG_LIMIT = 200;

let context: AudioContext | null = null;
let sounding: { stop: () => void } | null = null;
const rung = new Set<string>();

function audio(): AudioContext | null {
  if (typeof AudioContext === "undefined") return null;
  try {
    context ??= new AudioContext();
    if (context.state === "suspended") void context.resume();
    return context;
  } catch {
    return null;
  }
}

/** Plays ``[start, length, hz]`` notes (seconds from now) as the one sound of the phone. */
function play(notes: [number, number, number][], seconds: number): () => void {
  sounding?.stop();
  const ctx = audio();
  if (!ctx) return () => {};
  const out = ctx.createGain();
  out.gain.value = VOLUME;
  out.connect(ctx.destination);
  const now = ctx.currentTime;
  for (const [start, length, hz] of notes) {
    const osc = ctx.createOscillator();
    const env = ctx.createGain();
    osc.frequency.value = hz;
    // Short ramps: a tone cut off sharply clicks.
    env.gain.setValueAtTime(0, now + start);
    env.gain.linearRampToValueAtTime(1, now + start + 0.01);
    env.gain.setValueAtTime(1, now + start + length - 0.01);
    env.gain.linearRampToValueAtTime(0, now + start + length);
    osc.connect(env).connect(out);
    osc.start(now + start);
    osc.stop(now + start + length);
  }
  const self = {
    stop: () => {
      clearTimeout(timer);
      out.disconnect();
      if (sounding === self) sounding = null;
    },
  };
  const timer = setTimeout(self.stop, seconds * 1000);
  sounding = self;
  return self.stop;
}

/** Beeps of a call being put through, ``ms`` long; returns what stops them earlier. */
export function playRingback(ms: number = RINGBACK_MS): () => void {
  const seconds = ms / 1000;
  const notes: [number, number, number][] = [];
  for (let t = 0; t < seconds; t += BEEP_PERIOD_S) {
    notes.push([t, Math.min(BEEP_ON_S, seconds - t), BEEP_HZ]);
  }
  return play(notes, seconds);
}

/** Rings once for an incoming call: not again for the same call, not over another sound. */
export function ringOnce(callId: string): void {
  if (rung.has(callId)) return;
  if (rung.size >= RUNG_LIMIT) rung.clear();
  rung.add(callId);
  if (sounding) return;
  const notes: [number, number, number][] = [];
  for (const trill of [0, RING_TRILL_S + RING_GAP_S]) {
    for (let i = 0; i * RING_NOTE_S < RING_TRILL_S; i += 1) {
      notes.push([trill + i * RING_NOTE_S, RING_NOTE_S, RING_NOTES_HZ[i % 2]!]);
    }
  }
  play(notes, 2 * RING_TRILL_S + RING_GAP_S);
}

/** Rings for each call of ``callIds`` the first time it shows up. */
export function useIncomingRing(callIds: readonly string[]): void {
  const key = [...callIds].sort().join(",");
  useEffect(() => {
    for (const id of key.split(",")) if (id) ringOnce(id);
  }, [key]);
}
