// What the trainee reads when the microphone of a talk button stands in the way (stand check
// 20.10.2026: one text for «denied» and «no microphone» did not say what to do).

export const PTT_ASKING =
  "Браузер спрашивает доступ к микрофону — нажмите «Разрешить» в окошке вверху страницы.";
export const PTT_READY = "Микрофон включён. Теперь зажмите кнопку и говорите.";

/** What to do next with the talk button: hold it (push to talk) or press it once. */
export const HOLD_AGAIN = "снова зажмите кнопку";
export const PRESS_AGAIN = "снова нажмите кнопку микрофона";

/** Plain words for a microphone that would not open (``getUserMedia`` errors). */
export function pttProblem(error: unknown, again: string = HOLD_AGAIN): string {
  const name = (error as { name?: string } | null)?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return `Браузер не дал доступ к микрофону. Нажмите на значок слева от адреса сайта, разрешите микрофон и ${again}. Можно написать фразу текстом.`;
  }
  if (name === "NotFoundError" || name === "OverconstrainedError" || name === "NotSupportedError") {
    return `Микрофон не найден. Подключите гарнитуру и ${again}. Можно написать фразу текстом.`;
  }
  return `Микрофон занят другой программой (запись экрана, Zoom, Teams). Закройте её и ${again}.`;
}

// The browser answers at once when access is already settled; longer means it shows its prompt.
export const ASKING_AFTER_MS = 300;
