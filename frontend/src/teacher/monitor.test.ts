import type { MonitorOut, MonitorStudent } from "@/api/teacher";
import type { SessionEvent } from "@/emulator/ws";
import { applyEvent, initialState, summarize } from "@/teacher/monitor";

function student(i: number): MonitorStudent {
  return {
    student_id: `s${i}`,
    full_name: `Обучающийся ${i}`,
    login: `student${i}`,
    service_code: "territorial_oiv",
    active: [],
    finished: 0,
    passed: 0,
    average: null,
    last_total: null,
    last_attempt_id: null,
  };
}

function snapshot(count: number): MonitorOut {
  return {
    session_id: "sess",
    status: "running",
    norm_seconds: 30,
    pass_threshold: 70,
    cards_total: 3,
    students: Array.from({ length: count }, (_, i) => student(i + 1)),
    last_seq: 0,
  };
}

function first(state: { snapshot: MonitorOut }, index = 0): MonitorStudent {
  const s = state.snapshot.students[index];
  if (!s) throw new Error(`нет обучающегося ${index}`);
  return s;
}

let seq = 0;
function event(type: string, studentId: string | null, payload: Record<string, unknown>): SessionEvent {
  seq += 1;
  return { seq, type, session_id: "sess", student_id: studentId, payload, at: new Date(seq * 1000).toISOString() };
}

const T0 = "2026-09-18T09:00:00+00:00";

describe("monitor reducer", () => {
  it("follows a card from issue to evaluation", () => {
    let state = initialState(snapshot(2));
    state = applyEvent(state, event("attempt.issued", "s1", { attempt_id: "a1", card_number: "38260311", state: "issued", issued_at: T0 }));
    expect(first(state).active).toHaveLength(1);
    expect(state.untitled).toEqual(["a1"]);
    state = applyEvent(state, event("attempt.progress", "s1", { attempt_id: "a1", stage: "editing_status" }));
    expect(state.stages.s1?.stage).toBe("editing_status");
    expect(state.stages.s1?.attempt_id).toBe("a1");
    state = applyEvent(
      state,
      event("attempt.status_changed", "s1", { attempt_id: "a1", state: "in_progress", response_status: "accepted", primary_status_at: T0 }),
    );
    expect(first(state).active[0]?.response_status).toBe("accepted");
    state = applyEvent(state, event("card.status_changed", "s1", { attempt_id: "a1", card_status: "not_notified" }));
    expect(first(state).active[0]?.card_status).toBe("not_notified");
    state = applyEvent(state, event("attempt.evaluated", "s1", { attempt_id: "a1", total: 82, passed: true, state: "evaluated" }));
    const s1 = first(state);
    expect(s1.active).toEqual([]);
    expect(s1.finished).toBe(1);
    expect(s1.passed).toBe(1);
    expect(s1.average).toBe(82);
    expect(s1.last_total).toBe(82);
    expect(state.stages.s1).toBeUndefined();
    // The other trainee is untouched.
    expect(first(state, 1)).toEqual(student(2));
  });

  it("averages incrementally and ignores duplicates", () => {
    let state = initialState(snapshot(1));
    state = applyEvent(state, event("attempt.issued", "s1", { attempt_id: "a1", issued_at: T0 }));
    state = applyEvent(state, event("attempt.issued", "s1", { attempt_id: "a1", issued_at: T0 }));
    expect(first(state).active).toHaveLength(1);
    state = applyEvent(state, event("attempt.evaluated", "s1", { attempt_id: "a1", total: 60, passed: false }));
    state = applyEvent(state, event("attempt.issued", "s1", { attempt_id: "a2", issued_at: T0 }));
    state = applyEvent(state, event("attempt.evaluated", "s1", { attempt_id: "a2", total: 90, passed: true }));
    expect(first(state).average).toBe(75);
    expect(first(state).passed).toBe(1);
  });

  it("clears the cards when the session finishes", () => {
    let state = initialState(snapshot(1));
    state = applyEvent(state, event("attempt.issued", "s1", { attempt_id: "a1", issued_at: T0 }));
    state = applyEvent(state, event("session.finished", null, { session_id: "sess" }));
    expect(state.snapshot.status).toBe("finished");
    expect(first(state).active).toEqual([]);
  });

  it("handles 20 trainees with an event per second for a minute quickly", () => {
    let state = initialState(snapshot(20));
    const started = performance.now();
    for (let tick = 0; tick < 60; tick++) {
      for (let i = 1; i <= 20; i++) {
        const id = `a${i}-${tick}`;
        state = applyEvent(state, event("attempt.issued", `s${i}`, { attempt_id: id, issued_at: T0 }));
        state = applyEvent(state, event("attempt.progress", `s${i}`, { attempt_id: id, stage: "viewing" }));
        state = applyEvent(state, event("attempt.evaluated", `s${i}`, { attempt_id: id, total: 50 + (tick % 50), passed: tick % 2 === 0 }));
      }
    }
    const elapsed = performance.now() - started;
    expect(elapsed).toBeLessThan(500);
    expect(state.snapshot.students.every((s) => s.finished === 60 && s.active.length === 0)).toBe(true);
  });
});

describe("summarize", () => {
  const now = new Date("2026-09-18T09:01:00+00:00").getTime();
  const card = (id: string, issuedAt: string, primary: string | null) => ({
    attempt_id: id,
    card_number: id,
    incident_title: "",
    state: primary ? "in_progress" : "issued",
    response_status: primary ? "accepted" : "added",
    response_status_title: "",
    card_status: "registered",
    issued_at: issuedAt,
    received_at: null,
    primary_status_at: primary,
    submitted_at: null,
    total: null,
    passed: null,
  });

  it("shows the oldest card without a primary status and flags the overdue one", () => {
    const s = { ...student(1), active: [card("b", "2026-09-18T09:00:50+00:00", null), card("a", "2026-09-18T09:00:00+00:00", null)] };
    const summary = summarize(s, 30, 70, now, 3);
    expect(summary.current?.attempt_id).toBe("a");
    expect(summary.status).toBe("waiting");
    expect(summary.overdue).toBe(true);
    // The card the trainee is in wins over the oldest pending one.
    const inCard = summarize(s, 30, 70, now, 3, { stage: "viewing", at: T0, attempt_id: "b" });
    expect(inCard.current?.attempt_id).toBe("b");
    expect(inCard.overdue).toBe(true);
  });

  it("is idle without cards, done after the whole queue, low on a failed score", () => {
    expect(summarize(student(1), 30, 70, now, 3).status).toBe("idle");
    expect(summarize({ ...student(1), finished: 3 }, 30, 70, now, 3).status).toBe("done");
    const low = summarize({ ...student(1), last_total: 40 }, 30, 70, now, 3);
    expect(low.lowScore).toBe(true);
    const working = summarize({ ...student(1), active: [card("a", T0, T0)] }, 30, 70, now, 3);
    expect(working.status).toBe("working");
    expect(working.overdue).toBe(false);
  });
});
