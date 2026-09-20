import { useEffect, useState, type FormEvent } from "react";

import { useAdminSettings, useUpdateSettings, type AdminSettingsOut } from "@/api/admin";
import { ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"] as const;
const CODECS = ["opus", "g722", "alaw", "ulaw", "slin16", "slin"];

/** «Настройки» (PRD 13.7): telephony (ARI address, codecs, recording), logging, the backup
 * schedule and the default «Не завершено» threshold of new lessons. */
export function AdminSettingsPage() {
  const query = useAdminSettings();
  if (query.isPending) return <LoadingState text="Загружаем настройки…" />;
  if (query.isError) return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  return <SettingsForm settings={query.data} />;
}

function SettingsForm({ settings }: { settings: AdminSettingsOut }) {
  const update = useUpdateSettings();
  const t = settings.telephony;
  const [ariUrl, setAriUrl] = useState(t.ari_url);
  const [ring, setRing] = useState(String(t.ring_timeout_seconds));
  const [recording, setRecording] = useState(t.recording_enabled);
  const [webrtc, setWebrtc] = useState<Set<string>>(() => new Set(t.webrtc_codecs));
  const [phone, setPhone] = useState<Set<string>>(() => new Set(t.phone_codecs));
  const [level, setLevel] = useState(settings.logging.level);
  const [time, setTime] = useState(settings.backups.time);
  const [keep, setKeep] = useState(String(settings.backups.keep));
  const [unfinished, setUnfinished] = useState(String(Math.round(settings.training.unfinished_seconds / 3600)));
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!saved) return;
    const timer = setTimeout(() => setSaved(false), 4000);
    return () => clearTimeout(timer);
  }, [saved]);

  function submit(e: FormEvent) {
    e.preventDefault();
    update.mutate(
      {
        telephony: {
          ari_url: ariUrl.trim() || null,
          ring_timeout_seconds: Number(ring),
          recording_enabled: recording,
          webrtc_codecs: CODECS.filter((c) => webrtc.has(c)),
          phone_codecs: CODECS.filter((c) => phone.has(c)),
        },
        logging: { level },
        backups: { time, keep: Number(keep) },
        training: { unfinished_seconds: Number(unfinished) * 3600 },
      },
      { onSuccess: () => setSaved(true) },
    );
  }

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, codec: string) => {
    const next = new Set(set);
    if (next.has(codec)) next.delete(codec);
    else next.add(codec);
    setter(next);
  };

  return (
    <form onSubmit={submit} className="space-y-6" aria-label="Настройки">
      <div>
        <h1 className="text-2xl font-semibold">Настройки</h1>
        <p className="text-sm text-muted-foreground">Изменения записываются в журнал аудита.</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              Телефония {t.enabled ? <Badge tone="success">включена</Badge> : <Badge tone="neutral">выключена (профиль telephony)</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div className="space-y-1.5">
              <Label htmlFor="ari-url">Адрес ARI Asterisk</Label>
              <Input id="ari-url" value={ariUrl} onChange={(e) => setAriUrl(e.target.value)} placeholder="http://asterisk:8088/ari" />
              <p className="text-xs text-muted-foreground">Применяется после перезапуска API. Приложение ARI: {t.ari_app}, домен SIP: {t.sip_domain}.</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ring">Ожидание ответа, с</Label>
              <Input id="ring" type="number" min={5} max={300} value={ring} onChange={(e) => setRing(e.target.value)} className="w-32" />
            </div>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={recording} onChange={(e) => setRecording(e.target.checked)} /> Записывать разговоры (запись доступна в разборе)
            </label>
            <fieldset>
              <legend className="mb-1 font-medium">Кодеки софтфона в браузере</legend>
              <div className="flex flex-wrap gap-3">
                {CODECS.map((c) => (
                  <label key={c} className="flex items-center gap-1">
                    <input type="checkbox" checked={webrtc.has(c)} onChange={() => toggle(webrtc, setWebrtc, c)} /> {c}
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="mb-1 font-medium">Кодеки настольного телефона</legend>
              <div className="flex flex-wrap gap-3">
                {CODECS.map((c) => (
                  <label key={c} className="flex items-center gap-1">
                    <input type="checkbox" checked={phone.has(c)} onChange={() => toggle(phone, setPhone, c)} /> {c}
                  </label>
                ))}
              </div>
            </fieldset>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Журналирование</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 text-sm">
              <Label htmlFor="log-level">Уровень журнала API</Label>
              <select id="log-level" value={level} onChange={(e) => setLevel(e.target.value as typeof level)} className="h-9 w-40 rounded-md border border-input bg-background text-foreground px-2 text-sm">
                {LEVELS.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">Применяется сразу; фоновые задачи берут уровень при следующем запуске.</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Резервные копии</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="backup-time">Время ежедневной копии</Label>
                <Input id="backup-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="backup-keep">Хранить копий</Label>
                <Input id="backup-keep" type="number" min={1} max={365} value={keep} onChange={(e) => setKeep(e.target.value)} />
              </div>
              <p className="text-xs text-muted-foreground sm:col-span-2">Служба копий подхватывает расписание в течение минуты.</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Занятия</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 text-sm">
              <Label htmlFor="unfinished">Порог «Не завершено», часов</Label>
              <Input id="unfinished" type="number" min={1} max={336} value={unfinished} onChange={(e) => setUnfinished(e.target.value)} className="w-32" />
              <p className="text-xs text-muted-foreground">Через сколько часов после первичного статуса открытая карточка помечается «Не завершено» (памятка: 48 часов). Значение по умолчанию для новых занятий.</p>
            </CardContent>
          </Card>
        </div>
      </div>

      {update.isError && <ErrorState message={update.error.message} />}
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={update.isPending}>
          {update.isPending ? "Сохраняем…" : "Сохранить настройки"}
        </Button>
        {saved && (
          <span className="text-sm text-success" role="status">
            Сохранено.
          </span>
        )}
      </div>
    </form>
  );
}
