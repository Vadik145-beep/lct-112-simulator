/** «+7 922 000-00-01» for a stored 79220000001 (docs/MULTIFON.md); anything else as is. */
export function formatPhone(phone: string): string {
  const d = phone.replace(/\D/g, "");
  if (d.length !== 11) return phone;
  return `+${d[0]} ${d.slice(1, 4)} ${d.slice(4, 7)}-${d.slice(7, 9)}-${d.slice(9)}`;
}
