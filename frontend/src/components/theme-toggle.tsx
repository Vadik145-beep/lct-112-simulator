import { Moon, Sun } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getTheme, toggleTheme, type Theme } from "@/lib/theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => getTheme());
  const next = theme === "dark" ? "светлую" : "тёмную";
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Включить ${next} тему`}
      title={`Включить ${next} тему`}
      onClick={() => setTheme(toggleTheme())}
    >
      {theme === "dark" ? <Sun /> : <Moon />}
    </Button>
  );
}
