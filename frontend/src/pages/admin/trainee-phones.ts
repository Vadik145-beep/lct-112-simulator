/** Own phones of trainees rung through MultiFon (docs/MULTIFON.md): «student1 = 8 922 000-00-01»
 * per line in the settings form ⇄ the login → phone table of the telephony settings. */
export function phonesToText(
  phones: Record<string, string> | undefined,
): string {
  return Object.entries(phones ?? {})
    .map(([login, phone]) => `${login} = ${phone}`)
    .join("\n");
}

export function textToPhones(text: string): Record<string, string> {
  const phones: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const [login, ...rest] = line.split("=");
    const phone = rest.join("=").trim();
    if (login?.trim() && phone) phones[login.trim()] = phone;
  }
  return phones;
}
