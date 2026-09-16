import { BookOpen, Search } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useReferenceSearch } from "@/api/training";
import { ErrorState } from "@/components/states";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const EXAMPLES = ["не принята", "номер наряда", "мусоропровод", "дубль", "не оповещено"];

/** «Справочник»: search in the memo «Работа на АРМ-112» and in the incident classifier. */
export function StudentReferencePage() {
  const [params] = useSearchParams();
  const [text, setText] = useState(() => params.get("q") ?? "");
  const query = useReferenceSearch(text);
  const active = text.trim().length >= 2;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Справочник</h1>
        <p className="text-sm text-muted-foreground">
          Памятка «Работа на АРМ-112» и классификатор происшествий. Поиск по словам, регистр не важен.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="reference-search">Что ищем</Label>
        <div className="relative">
          <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground" aria-hidden />
          <Input
            id="reference-search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="например, «не принята» или «мусоропровод»"
            className="pl-9"
            autoFocus
          />
        </div>
        {!active && (
          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span>Попробуйте:</span>
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => setText(example)}
                className="rounded-full border px-2 py-0.5 hover:bg-accent"
              >
                {example}
              </button>
            ))}
          </div>
        )}
      </div>

      {query.isError && <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />}

      {active && query.data && (
        <div className="grid gap-6 lg:grid-cols-2">
          <section className="space-y-2">
            <h2 className="flex items-center gap-2 text-lg font-medium">
              <BookOpen className="size-4" aria-hidden /> Памятка
              <span className="text-sm font-normal text-muted-foreground">({query.data.memo.length})</span>
            </h2>
            {query.data.memo.length === 0 ? (
              <p className="text-sm text-muted-foreground">В памятке таких слов нет. Попробуйте другую форму слова.</p>
            ) : (
              <ul className="space-y-2">
                {query.data.memo.map((hit, i) => (
                  <li key={i} className="rounded-lg border bg-card p-3 text-sm">
                    <div className="mb-1 text-xs text-muted-foreground">стр. {hit.page}</div>
                    <Highlight text={hit.text} query={text} />
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="space-y-2">
            <h2 className="text-lg font-medium">
              Классификатор{" "}
              <span className="text-sm font-normal text-muted-foreground">({query.data.types.length})</span>
            </h2>
            {query.data.types.length === 0 ? (
              <p className="text-sm text-muted-foreground">Типов происшествий с такими словами нет.</p>
            ) : (
              <ul className="divide-y rounded-lg border bg-card text-sm">
                {query.data.types.map((t) => (
                  <li key={t.code} className="px-3 py-2">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-medium">{t.final_title}</span>
                      <span className="font-mono text-xs text-muted-foreground">{t.code}</span>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {t.group_title} · {t.signs.join(" → ")}
                      {t.main_service ? ` · основная служба: ${t.main_service}` : ""}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function Highlight({ text, query }: { text: string; query: string }) {
  const words = query.toLowerCase().replace(/ё/g, "е").split(/\s+/).filter((w) => w.length >= 2);
  if (words.length === 0) return <>{text}</>;
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/е/g, "[её]"));
  const source = `(${escaped.join("|")})`;
  const splitter = new RegExp(source, "gi");
  const matcher = new RegExp(`^${source}$`, "i");
  return (
    <>
      {text.split(splitter).map((part, i) =>
        matcher.test(part) ? (
          <mark key={i} className="rounded bg-warning/40 px-0.5">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}
