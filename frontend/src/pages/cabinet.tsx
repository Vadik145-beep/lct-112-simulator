import { useQuery } from "@tanstack/react-query";

import { api, errorMessage, type Role } from "@/api/client";
import { ErrorState, LoadingState } from "@/components/states";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const CABINET_PATH = {
  student: "/api/student/cabinet",
  teacher: "/api/teacher/cabinet",
  admin: "/api/admin/cabinet",
} as const;

/** Empty cabinet: asks the role endpoint (so 403 is enforced by the backend) and lists sections. */
export function CabinetPage({ role }: { role: Role }) {
  const query = useQuery({
    queryKey: ["cabinet", role],
    queryFn: async () => {
      const { data, error } = await api.GET(CABINET_PATH[role]);
      if (!data) throw new Error(errorMessage(error));
      return data;
    },
  });

  if (query.isPending) return <LoadingState text="Открываем кабинет…" />;
  if (query.isError) {
    return <ErrorState message={query.error.message} onRetry={() => void query.refetch()} />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">{query.data.title}</h1>
        <p className="text-sm text-muted-foreground">Разделы появятся по мере готовности системы.</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {query.data.sections.map((section) => (
          <Card key={section}>
            <CardHeader>
              <CardTitle>{section}</CardTitle>
              <CardDescription>Пока пусто</CardDescription>
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              Здесь пока ничего нет. Содержимое раздела появится в следующих версиях.
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
