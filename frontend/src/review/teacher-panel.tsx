import { MessageSquare, Pencil } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAddComment, useOverrideEvaluation } from "@/api/review";
import type { AttemptOut } from "@/api/training";
import { ErrorState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDateTime } from "@/emulator/time";
import { cn } from "@/lib/utils";

/** The total of the review (PRD 13.6). With a teacher's override the old value is struck
 * through and the reason is shown under the box. */
/** The total with the verdict; ``reasons`` are the critical errors that block the pass
 * (issue #69), shown under the box so the trainee sees why the attempt failed. */
export function ScoreBox({ total, passed, override, reasons = [] }: { total: number; passed: boolean; override: AttemptOut["override"]; reasons?: string[] }) {
  return (
    <div className="flex flex-col items-end gap-1">
      <div className={cn("flex items-center gap-4 rounded-xl border px-5 py-3", passed ? "border-success/50 bg-success/10" : "border-destructive/50 bg-destructive/10")}>
        {override && (
          <div className="text-2xl text-muted-foreground line-through tabular-nums" data-testid="review-old-total" aria-label="Прежняя оценка">
            {override.old_total}
          </div>
        )}
        <div className="text-4xl font-semibold tabular-nums" data-testid="review-total">
          {total}
        </div>
        <div className="leading-tight">
          <div className="text-xs text-muted-foreground">из 100</div>
          <div className={cn("font-semibold", passed ? "text-success" : "text-destructive")} data-testid="review-verdict">
            {passed ? "Зачтено" : "Не зачтено"}
          </div>
        </div>
      </div>
      {!passed && reasons.length > 0 && (
        <ul className="max-w-sm space-y-0.5 text-right text-xs text-destructive" data-testid="review-blockers" aria-label="Причина незачёта">
          {reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      {override && (
        <p className="max-w-sm text-right text-xs text-muted-foreground" data-testid="review-override-reason">
          Изменено преподавателем {override.teacher_name && `(${override.teacher_name})`} {formatDateTime(override.at)}: {override.reason}
        </p>
      )}
    </div>
  );
}

/** Comments of the teacher under the review; the trainee reads, the teacher can add. The
 * teacher also changes the total here, always with a reason (PRD 3). */
export function ReviewNotes({ attempt, teacher, total }: { attempt: AttemptOut; teacher: boolean; total: number }) {
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Comments attempt={attempt} teacher={teacher} />
      {teacher && <OverrideForm attempt={attempt} total={total} />}
    </div>
  );
}

function Comments({ attempt, teacher }: { attempt: AttemptOut; teacher: boolean }) {
  const add = useAddComment(attempt.id, attempt.session.id);
  const [text, setText] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    const value = text.trim();
    if (!value) return;
    add.mutate(value, { onSuccess: () => setText("") });
  }

  return (
    <Card data-testid="review-comments">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <MessageSquare className="size-4" aria-hidden /> Комментарии преподавателя
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {attempt.comments.length === 0 ? (
          <p className="text-muted-foreground">{teacher ? "Комментариев пока нет." : "Преподаватель ещё не оставил комментариев."}</p>
        ) : (
          <ul className="space-y-2">
            {attempt.comments.map((c) => (
              <li key={c.id} className="rounded-md border p-3">
                <div className="text-xs text-muted-foreground">
                  {c.author_name || "Преподаватель"} · {formatDateTime(c.at)}
                </div>
                <p className="mt-1 whitespace-pre-wrap">{c.text}</p>
              </li>
            ))}
          </ul>
        )}
        {teacher && (
          <form onSubmit={submit} className="space-y-2" aria-label="Новый комментарий">
            <Label htmlFor="comment-text">Комментарий обучающемуся</Label>
            <textarea
              id="comment-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              maxLength={4000}
              rows={3}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus-visible:ring-1 focus-visible:ring-ring focus-visible:outline-none"
              placeholder="Что стоит учесть в следующий раз"
            />
            {add.isError && <ErrorState message={add.error.message} />}
            <Button type="submit" size="sm" disabled={add.isPending || !text.trim()}>
              {add.isPending ? "Отправляем…" : "Отправить комментарий"}
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function OverrideForm({ attempt, total }: { attempt: AttemptOut; total: number }) {
  const override = useOverrideEvaluation(attempt.id, attempt.session.id);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(String(total));
  const [reason, setReason] = useState("");
  const reasonMissing = reason.trim().length < 3;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (reasonMissing) return;
    override.mutate(
      { new_total: Number(value), reason: reason.trim() },
      {
        onSuccess: () => {
          setOpen(false);
          setReason("");
        },
      },
    );
  }

  return (
    <Card data-testid="review-override">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Pencil className="size-4" aria-hidden /> Оценка преподавателя
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          Итог можно изменить только с указанием причины: она записывается в журнал аудита и видна обучающемуся, прежняя оценка остаётся в разборе зачёркнутой.
        </p>
        {!open ? (
          <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
            Изменить оценку
          </Button>
        ) : (
          <form onSubmit={submit} className="space-y-3" aria-label="Изменение оценки">
            <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
              <div className="space-y-1.5">
                <Label htmlFor="override-total">Новый итог</Label>
                <Input id="override-total" type="number" min={0} max={100} step={1} required value={value} onChange={(e) => setValue(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="override-reason">Причина (обязательно)</Label>
                <Input id="override-reason" required minLength={3} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Например: комментарий к работам по существу, ошибка детектора" />
              </div>
            </div>
            {reasonMissing && reason.length > 0 && <p className="text-xs text-destructive">Причина — хотя бы три символа.</p>}
            {override.isError && <ErrorState message={override.error.message} />}
            <div className="flex flex-wrap gap-2">
              <Button type="submit" size="sm" disabled={override.isPending || reasonMissing}>
                {override.isPending ? "Сохраняем…" : "Сохранить новую оценку"}
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => setOpen(false)}>
                Отмена
              </Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
