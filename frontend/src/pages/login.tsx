import { GraduationCap, Shield, UserRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { type Role } from "@/api/client";
import { usePublicConfig } from "@/api/config";
import { useAuth } from "@/app/use-auth";
import { FullScreenLoading } from "@/components/states";
import { ROLE_HOME, ROLE_TITLES } from "@/lib/roles";

const DEMO_BUTTONS: { role: Role; label: string; icon: typeof UserRound }[] = [
  { role: "teacher", label: "Войти как преподаватель", icon: GraduationCap },
  { role: "student", label: "Войти как обучающийся", icon: UserRound },
  { role: "admin", label: "Войти как администратор", icon: Shield },
];
const ROLE_ORDER: Role[] = DEMO_BUTTONS.map((b) => b.role);

// The entry screen follows the customer's АРМ-112 («ВХОД В СИСТЕМУ», recommendation 4):
// a city in light blue, the form on the right with underlined fields and a grey button.
const field =
  "h-9 w-full border-0 border-b border-white/80 bg-transparent px-1 text-lg text-white outline-none placeholder:text-white/60 focus:border-white";

export function LoginPage() {
  const { status, user, login, demoLogin } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [loginValue, setLoginValue] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"form" | Role | null>(null);

  const config = usePublicConfig();
  // DEMO_MODE or QUICK_LOGIN (showing the stand): one-click entry, the server names the roles.
  const quickRoles = new Set(config.data?.quick_login_roles ?? []);
  const quickButtons = DEMO_BUTTONS.filter(({ role }) => quickRoles.has(role));
  // SHOW_DEMO_CREDENTIALS: logins and passwords by role (recommendation 2).
  const credentials = [...(config.data?.demo_credentials ?? [])].sort(
    (a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role),
  );

  // Do not offer the form while a previous session may still be restored.
  if (status === "loading") return <FullScreenLoading />;
  if (status === "authenticated" && user) {
    // Return to the page the user came from only if it belongs to their own cabinet.
    const from = (location.state as { from?: string } | null)?.from;
    const home = ROLE_HOME[user.role];
    return <Navigate to={from && from.startsWith(home) ? from : home} replace />;
  }

  async function run(action: () => Promise<void>, kind: "form" | Role) {
    setBusy(kind);
    setError(null);
    try {
      await action();
      navigate("/", { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось войти. Попробуйте ещё раз.");
    } finally {
      setBusy(null);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void run(() => login(loginValue, password), "form");
  }

  const quickLogin = (role: Role) => void run(() => demoLogin(role), role);

  return (
    <div className="relative min-h-dvh overflow-hidden bg-gradient-to-b from-[#6aa9d6] to-[#a8d1ec] text-white">
      <CityBackdrop />
      <Helicopter />
      {/* Fades the buildings behind the form and the table so the text stays readable. */}
      <div className="pointer-events-none absolute inset-0 bg-[#7fb5dc]/75 lg:bg-transparent lg:bg-gradient-to-l lg:from-[#7fb5dc]/90 lg:from-40% lg:to-transparent lg:to-65%" />
      <div className="relative z-10 flex min-h-dvh items-center justify-center px-4 py-10 lg:justify-end lg:pr-[8vw]">
        <div className="w-full max-w-[540px]">
          <div className="flex items-center gap-5">
            <span className="text-[88px] leading-none font-light tracking-tight">112</span>
            <span className="text-3xl leading-tight font-light uppercase">Вход в систему</span>
          </div>

          <form onSubmit={onSubmit} className="mt-8 space-y-5" noValidate>
            <div>
              <label htmlFor="login" className="block text-xs text-white/85 lowercase after:content-[':']">
                Логин
              </label>
              <input
                id="login"
                name="login"
                autoComplete="username"
                autoFocus
                required
                className={field}
                value={loginValue}
                onChange={(e) => setLoginValue(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="password" className="block text-xs text-white/85 lowercase after:content-[':']">
                Пароль
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                className={field}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
            {error && (
              <p className="rounded bg-[#b3261e]/90 px-3 py-2 text-sm text-white" role="alert">
                {error}
              </p>
            )}
            <button
              type="submit"
              className="h-12 w-full bg-[#7b7b7b] text-lg font-medium tracking-wide text-white uppercase transition-colors hover:bg-[#6a6a6a] disabled:hover:bg-[#7b7b7b]"
              disabled={busy !== null || !loginValue || !password}
            >
              {busy === "form" ? "Входим…" : "Войти"}
            </button>
          </form>

          {credentials.length > 0 ? (
            <section className="mt-8 rounded bg-[#2f5f86]/70 p-4 backdrop-blur-sm" aria-labelledby="demo-access">
              <h2 id="demo-access" className="text-base font-medium">
                Демонстрационный доступ · учебная система
              </h2>
              <table className="mt-2 w-full text-sm">
                <thead className="text-left text-xs text-white/75">
                  <tr>
                    <th className="py-1 pr-3 font-normal">Роль</th>
                    <th className="py-1 pr-3 font-normal">Логин</th>
                    <th className="py-1 pr-3 font-normal">Пароль</th>
                    <th className="py-1 font-normal" />
                  </tr>
                </thead>
                <tbody>
                  {credentials.map((c) => {
                    const button = quickButtons.find((b) => b.role === c.role);
                    return (
                      <tr key={c.role} className="border-t border-white/20">
                        <td className="py-1.5 pr-3">{ROLE_TITLES[c.role]}</td>
                        <td className="py-1.5 pr-3 font-mono">{c.login}</td>
                        <td className="py-1.5 pr-3 font-mono">{c.password}</td>
                        <td className="py-1.5 text-right">
                          {button && (
                            <button
                              type="button"
                              aria-label={button.label}
                              className="rounded bg-white/90 px-3 py-1 text-sm font-medium text-[#24557d] hover:bg-white disabled:opacity-70"
                              disabled={busy !== null}
                              onClick={() => quickLogin(c.role)}
                            >
                              {busy === c.role ? "Входим…" : "Войти"}
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          ) : (
            quickButtons.length > 0 && (
              <section className="mt-8 space-y-2" aria-label="Вход без пароля">
                <p className="text-xs text-white/85">
                  {config.data?.demo_mode ? "Демо-режим: вход без пароля" : "Вход в демонстрационный стенд без пароля"}
                </p>
                {quickButtons.map(({ role, label, icon: Icon }) => (
                  <button
                    key={role}
                    type="button"
                    className="flex h-10 w-full items-center gap-2 rounded bg-white/90 px-3 text-sm font-medium text-[#24557d] hover:bg-white disabled:opacity-70"
                    disabled={busy !== null}
                    onClick={() => quickLogin(role)}
                  >
                    <Icon className="size-4" aria-hidden /> {busy === role ? "Входим…" : label}
                  </button>
                ))}
              </section>
            )
          )}
          {config.isError && (
            <p className="mt-4 text-xs text-white/85">Сервер не ответил на запрос настроек. Обычный вход по логину и паролю доступен.</p>
          )}
        </div>
      </div>
    </div>
  );
}

/** The helicopter over the city, as on the original entry screen. */
function Helicopter() {
  return (
    <svg
      className="pointer-events-none absolute top-[18%] left-[34%] hidden w-[170px] lg:block"
      viewBox="0 0 170 70"
      aria-hidden
    >
      <rect x="10" y="4" width="120" height="3" rx="1.5" fill="#3d4f5c" />
      <rect x="68" y="7" width="4" height="9" fill="#3d4f5c" />
      <path d="M40 30 Q44 16 70 16 L92 16 Q112 18 118 30 L160 22 L162 28 L118 40 Q110 48 80 48 L52 48 Q38 46 40 30 Z" fill="#ffffff" />
      <path d="M42 40 L118 40 Q110 48 80 48 L52 48 Q42 46 42 40 Z" fill="#e8582c" />
      <path d="M50 30 Q54 21 68 21 L74 21 L74 32 L50 32 Z" fill="#9fd2f0" />
      <circle cx="161" cy="25" r="7" fill="none" stroke="#3d4f5c" strokeWidth="1.5" />
      <path d="M58 48 L56 58 M96 48 L98 58 M44 58 L110 58" stroke="#3d4f5c" strokeWidth="2.5" fill="none" />
    </svg>
  );
}

/** Light-blue skyline behind the form, drawn here (no external images). */
function CityBackdrop() {
  return (
    <svg
      className="pointer-events-none absolute inset-x-0 bottom-0 h-[85%] w-full"
      viewBox="0 0 1600 900"
      preserveAspectRatio="xMidYMax slice"
      aria-hidden
    >
      <g fill="#b9daf0" opacity="0.9">
        <rect x="40" y="120" width="150" height="780" />
        <rect x="230" y="60" width="120" height="840" />
        <rect x="620" y="300" width="140" height="600" />
        <rect x="990" y="380" width="110" height="520" />
      </g>
      <g fill="#8ec0e3">
        <rect x="0" y="260" width="120" height="640" />
        <rect x="170" y="10" width="90" height="890" />
        <rect x="420" y="160" width="110" height="740" />
        <rect x="540" y="330" width="120" height="570" />
        <rect x="780" y="250" width="70" height="650" />
        <rect x="870" y="400" width="140" height="500" />
      </g>
      <g fill="#5f98c4">
        <rect x="300" y="200" width="70" height="700" />
        <rect x="700" y="420" width="90" height="480" />
        <rect x="1130" y="560" width="80" height="340" />
      </g>
      <g fill="#ffffff" opacity="0.18">
        {Array.from({ length: 14 }, (_, i) => (
          <rect key={i} x={440} y={190 + i * 48} width={70} height={10} />
        ))}
        {Array.from({ length: 10 }, (_, i) => (
          <rect key={`b${i}`} x={560} y={360 + i * 52} width={80} height={10} />
        ))}
      </g>
      <g fill="#eef3f7">
        <rect x="80" y="700" width="260" height="200" />
        <rect x="380" y="760" width="300" height="140" />
        <rect x="1180" y="720" width="380" height="180" />
        <rect x="1000" y="800" width="160" height="100" />
      </g>
      <g fill="#c9d3dc">
        {Array.from({ length: 4 }, (_, i) => (
          <rect key={`w${i}`} x={110} y={730 + i * 40} width={200} height={12} />
        ))}
        {Array.from({ length: 4 }, (_, i) => (
          <rect key={`v${i}`} x={1210} y={750 + i * 36} width={320} height={12} />
        ))}
      </g>
    </svg>
  );
}
