import { Phone } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useMyProfile, useSetMyPhone } from "@/api/telephony";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatPhone } from "@/lib/phone";

/** A lesson with the live call on the number the teacher entered: the calls ring it. */
export function LessonPhoneNotice({ phone }: { phone: string }) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="lesson-phone-notice">
      <Phone className="size-4 text-primary" aria-hidden />
      <span>
        Вызовы звонят на телефон <strong>{formatPhone(phone)}</strong>: возьмите трубку.
      </span>
    </div>
  );
}

/** A lesson with calls to the phone (docs/MULTIFON.md): the calls ring the trainee's own
 * phone, so the page shows the number and asks for it when there is none yet. */
export function PhoneCallsNotice() {
  const profile = useMyProfile();
  const phone = profile.data?.phone ?? null;
  const [editing, setEditing] = useState(false);

  // No number yet: the window opens by itself, the calls have nowhere to ring.
  useEffect(() => {
    if (profile.isSuccess && !phone) setEditing(true);
  }, [profile.isSuccess, phone]);

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="phone-calls-notice">
      <Phone className="size-4 text-primary" aria-hidden />
      {phone ? (
        <span>
          Вызовы звонят на ваш телефон <strong data-testid="my-phone">{formatPhone(phone)}</strong>. Звонки в службу и заявителю из
          карточки тоже приходят на него: возьмите трубку.
        </span>
      ) : (
        <span>Вызовы этого занятия звонят на телефон — укажите свой номер.</span>
      )}
      <Button type="button" variant="outline" size="sm" onClick={() => setEditing(true)}>
        {phone ? "Изменить номер" : "Указать номер"}
      </Button>
      {editing && <PhoneDialog current={phone} onClose={() => setEditing(false)} />}
    </div>
  );
}

function PhoneDialog({ current, onClose }: { current: string | null; onClose: () => void }) {
  const save = useSetMyPhone();
  const [value, setValue] = useState(current ? formatPhone(current) : "");

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(value.trim(), { onSuccess: () => onClose() });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="presentation">
      <form
        onSubmit={submit}
        role="dialog"
        aria-modal="true"
        aria-labelledby="phone-dialog-title"
        className="w-full max-w-sm space-y-4 rounded-lg border bg-card p-5 text-card-foreground shadow-lg"
      >
        <div className="space-y-1">
          <h2 id="phone-dialog-title" className="text-lg font-semibold">
            Ваш номер телефона
          </h2>
          <p className="text-sm text-muted-foreground">
            На него придут учебные вызовы. Номер виден только вам и преподавателю тренажёра.
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="my-phone-input">Мобильный</Label>
          <Input
            id="my-phone-input"
            type="tel"
            inputMode="tel"
            autoFocus
            placeholder="8 922 000-00-00"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          {save.isError && (
            <p className="text-xs text-destructive" role="alert">
              {save.error.message}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Позже
          </Button>
          <Button type="submit" disabled={save.isPending || !value.trim()}>
            Сохранить
          </Button>
        </div>
      </form>
    </div>
  );
}
