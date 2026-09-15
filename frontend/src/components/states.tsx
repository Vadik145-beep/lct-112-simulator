import { AlertTriangle, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";

export function LoadingState({ text = "Загрузка…" }: { text?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground" role="status">
      <Loader2 className="size-5 animate-spin" aria-hidden />
      <span>{text}</span>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      className="flex flex-col items-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-6 text-center"
      role="alert"
    >
      <AlertTriangle className="size-6 text-destructive" aria-hidden />
      <p className="text-sm">{message}</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  );
}

export function FullScreenLoading() {
  return (
    <div className="flex min-h-dvh items-center justify-center">
      <LoadingState text="Проверяем сессию…" />
    </div>
  );
}
