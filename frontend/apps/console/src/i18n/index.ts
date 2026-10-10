import { useMemo, useSyncExternalStore } from "react";
import { resolveLocale, translate, translateDiagnostic, type Locale } from "./locale";
import type { MessageKey } from "./messages";

function subscribe(onChange: () => void) {
  window.addEventListener("languagechange", onChange);
  return () => window.removeEventListener("languagechange", onChange);
}

function browserLocale(): Locale {
  return resolveLocale(navigator.languages.length ? navigator.languages : [navigator.language]);
}

export function useLocale() {
  const locale = useSyncExternalStore(subscribe, browserLocale, () => "en" as const);
  return useMemo(
    () => ({
      locale,
      t: (key: MessageKey, parameters?: Record<string, string | number>) =>
        translate(locale, key, parameters),
      message: (value: string) => translateDiagnostic(locale, value),
      date: (value: string) => new Date(value).toLocaleString(locale),
    }),
    [locale],
  );
}
