import { Construction } from "lucide-react";

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <Construction className="size-8 text-muted-foreground" aria-hidden />
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-sm text-muted-foreground">Раздел в разработке. Пока здесь ничего нет.</p>
    </div>
  );
}
