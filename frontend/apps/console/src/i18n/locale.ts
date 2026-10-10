import { chinese, diagnostics, type MessageKey } from "./messages.ts";

export type Locale = "en" | "zh-CN";
type Parameters = Record<string, string | number>;

export function resolveLocale(languages: readonly string[]): Locale {
  for (const language of languages) {
    const base = language.toLowerCase().split(/[-_]/)[0];
    if (base === "zh") return "zh-CN";
    if (base === "en") return "en";
  }
  return "en";
}

export function translate(locale: Locale, key: MessageKey, parameters: Parameters = {}): string {
  const template = locale === "zh-CN" ? chinese[key] : key;
  return template.replace(/\{(\w+)\}/g, (placeholder, name: string) =>
    Object.hasOwn(parameters, name) ? String(parameters[name]) : placeholder,
  );
}

// Preserve unknown upstream diagnostics. Never translate conversation content or user names.
export function translateDiagnostic(locale: Locale, message: string): string {
  if (locale === "en") return message;
  if (Object.hasOwn(chinese, message)) return chinese[message as MessageKey];
  if (Object.hasOwn(diagnostics, message)) return diagnostics[message];
  if (message.startsWith("Value error, "))
    return translateDiagnostic(locale, message.slice("Value error, ".length));
  const length = message.match(/^String should have (at least|at most) (\d+) characters?$/);
  if (length) return `文本${length[1] === "at least" ? "至少" : "最多"}需要 ${length[2]} 个字符`;
  if (message.startsWith("Request failed (") && message.endsWith(")."))
    return translate(locale, "Request failed ({status}).", { status: message.slice(16, -2) });
  if (message.includes("; "))
    return message
      .split("; ")
      .map((item) => translateDiagnostic(locale, item))
      .join("；");
  return message;
}
