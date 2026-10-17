/** Advice for further training (issue #265): template points from what the evaluation found,
 * after a card and after a lesson. */
export function AdviceList({ title, points }: { title: string; points: string[] }) {
  if (points.length === 0) return null;
  return (
    <div className="space-y-1 text-sm" data-testid="advice">
      <div className="font-medium">{title}</div>
      <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
        {points.map((tip, i) => (
          <li key={i}>{tip}</li>
        ))}
      </ul>
    </div>
  );
}
