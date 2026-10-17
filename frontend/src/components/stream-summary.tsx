import type { StreamSummary } from "@/api/review";

/** «Вал карточек» (issue #264): how the trainee held the whole stream, with template advice. The
 * scores of the cards stay as they are; this sits next to them. */
export function StreamSummaryView({ summary }: { summary: StreamSummary }) {
  const s = summary;
  const emergency =
    s.emergency_first === null ? "ЧС в вале не было" : s.emergency_first ? "ЧС принята первой" : "ЧС принята позже обычных";
  return (
    <div className="space-y-1 text-sm" data-testid="stream-summary">
      <div className="font-medium">Вал карточек</div>
      <p>
        Пришло {s.arrived}, закрыто {s.closed} · позже норматива {s.late} · без реакции {s.no_reaction} · {emergency}
      </p>
      <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
        {s.advice.map((tip, i) => (
          <li key={i}>{tip}</li>
        ))}
      </ul>
    </div>
  );
}
