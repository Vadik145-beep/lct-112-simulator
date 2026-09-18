import { useAdminServices, useUpdateService } from "@/api/admin";
import { ErrorState, LoadingState } from "@/components/states";

/** «Службы» (PRD 13.7): whether a service is alerted through АРМ-112 (the trainer's
 * audience) and whether it may refuse a card (service 103 never does). */
export function AdminServicesPage() {
  const services = useAdminServices();
  const update = useUpdateService();

  if (services.isPending) return <LoadingState text="Загружаем службы…" />;
  if (services.isError) return <ErrorState message={services.error.message} onRetry={() => void services.refetch()} />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Службы</h1>
        <p className="text-sm text-muted-foreground">
          «Через АРМ-112» — служба получает карточки на рабочем месте эмулятора; «без отказа» — служба не ставит «Не принята» (как 103).
        </p>
      </div>
      {update.isError && <ErrorState message={update.error.message} />}
      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full min-w-[560px] text-sm" aria-label="Службы">
          <thead className="text-left text-xs text-muted-foreground">
            <tr className="border-b">
              <th className="py-2 pl-3 pr-3 font-medium">Код</th>
              <th className="py-2 pr-3 font-medium">Служба</th>
              <th className="py-2 pr-3 font-medium">Через АРМ-112</th>
              <th className="py-2 pr-3 font-medium">Без отказа</th>
            </tr>
          </thead>
          <tbody>
            {services.data.map((s) => (
              <tr key={s.code} className="border-b last:border-0" data-service={s.code}>
                <td className="py-2 pl-3 pr-3 font-mono text-xs">{s.code}</td>
                <td className="py-2 pr-3">
                  <div className="font-medium">{s.short_title}</div>
                  <div className="text-xs text-muted-foreground">{s.title}</div>
                </td>
                <td className="py-2 pr-3">
                  <input
                    type="checkbox"
                    checked={s.via_arm112}
                    disabled={update.isPending}
                    onChange={(e) => update.mutate({ code: s.code, via_arm112: e.target.checked })}
                    aria-label={`Через АРМ-112: ${s.short_title}`}
                  />
                </td>
                <td className="py-2 pr-3">
                  <input
                    type="checkbox"
                    checked={s.no_reject}
                    disabled={update.isPending}
                    onChange={(e) => update.mutate({ code: s.code, no_reject: e.target.checked })}
                    aria-label={`Без отказа: ${s.short_title}`}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
