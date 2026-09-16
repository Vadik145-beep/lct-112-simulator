import { useEffect, useRef, useState } from "react";

import { refreshAccessToken } from "@/api/client";
import { getAccessToken } from "@/api/token";

export interface SessionEvent {
  seq: number;
  type: string;
  session_id: string;
  student_id: string | null;
  payload: Record<string, unknown>;
  at: string;
}

// Reconnect pauses (PRD section 12): 1, 2, 4, 8, then 10 s between attempts.
const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 10000];
const CLOSE_UNAUTHORIZED = 4401;

export type ConnectionState = "connecting" | "online" | "offline";

/**
 * Live events of a training session. Keeps the last sequence number and reconnects with
 * `after_seq`, so a short outage loses nothing. `onEvent` receives every event in order.
 */
export function useSessionEvents(
  sessionId: string | undefined,
  initialSeq: number | undefined,
  onEvent: (event: SessionEvent) => void,
): ConnectionState {
  const [state, setState] = useState<ConnectionState>("connecting");
  const [seqKnown, setSeqKnown] = useState(false);
  const lastSeq = useRef<number | null>(null);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  // The first known sequence comes with the journal/attempt response; connect once it is here.
  useEffect(() => {
    if (initialSeq === undefined || lastSeq.current !== null) return;
    lastSeq.current = initialSeq;
    setSeqKnown(true);
  }, [initialSeq]);

  useEffect(() => {
    if (!sessionId || !seqKnown) return;
    let socket: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closed) return;
      setState((s) => (s === "online" ? "online" : "connecting"));
      const proto = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${location.host}/ws/sessions/${sessionId}?after_seq=${lastSeq.current ?? 0}`);
      socket.onopen = () => socket?.send(JSON.stringify({ token: getAccessToken() }));
      socket.onmessage = (message) => {
        const data = JSON.parse(message.data as string) as SessionEvent | { type: "ready" | "pong"; seq?: number };
        if (data.type === "ready") {
          attempt = 0;
          setState("online");
          return;
        }
        if (data.type === "pong" || !("seq" in data)) return;
        const event = data as SessionEvent;
        if (lastSeq.current !== null && event.seq <= lastSeq.current) return;
        lastSeq.current = event.seq;
        handler.current(event);
      };
      socket.onclose = async (ev) => {
        if (closed) return;
        setState("offline");
        if (ev.code === CLOSE_UNAUTHORIZED) await refreshAccessToken();
        const delay = RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)];
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
      socket.onerror = () => socket?.close();
    };
    connect();

    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [sessionId, seqKnown]);

  return state;
}
