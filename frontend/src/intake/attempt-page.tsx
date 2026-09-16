import { Navigate, useParams } from "react-router-dom";

import { useAttempt } from "@/api/training";
import { ErrorState, LoadingState } from "@/components/states";
import { CardPage } from "@/emulator/card-page";
import { CallCard } from "@/intake/call-page";

/** `/student/attempts/:attemptId`: the DDS card for a card-response attempt, the operator-112
 * card for a call-intake one. */
export function AttemptPage() {
  const { attemptId } = useParams<{ attemptId: string }>();
  if (!attemptId) return <Navigate to="/student" replace />;
  return <Dispatch attemptId={attemptId} />;
}

function Dispatch({ attemptId }: { attemptId: string }) {
  const query = useAttempt(attemptId);
  if (query.isPending) return <LoadingState text="Открываем…" />;
  if (query.isError || !query.data) {
    return (
      <div className="p-6">
        <ErrorState message={query.error?.message ?? "Не удалось открыть карточку."} onRetry={() => void query.refetch()} />
      </div>
    );
  }
  if (query.data.session.mode === "call_intake") return <CallCard attempt={query.data} />;
  return <CardPage />;
}
