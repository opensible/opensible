/**
 * Lightweight i18n helper.
 * Locales: en (default), km (Khmer), ko (Korean).
 * Dictionaries are split by feature in src/lib/i18n/<locale>/*.ts
 * and aggregated in src/lib/i18n/<locale>/index.ts.
 *
 * Usage:
 *   const t = useT();
 *   t("cloud.summary.title")
 */
import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { en } from "./i18n/en";
import { km } from "./i18n/km";
import { ko } from "./i18n/ko";

export type Locale = "en" | "km" | "ko";

export const LOCALES: { code: Locale; label: string; nativeLabel: string; flag: string }[] = [
  { code: "en", label: "English", nativeLabel: "English", flag: "🇺🇸" },
  { code: "km", label: "Khmer", nativeLabel: "ខ្មែរ", flag: "🇰🇭" },
  { code: "ko", label: "Korean", nativeLabel: "한국어", flag: "🇰🇷" },
];

const DICTS: Record<Locale, Record<string, string>> = { en, km, ko };
const KEY = "opensible.locale";

type Ctx = {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (k: string, vars?: Record<string, string | number>) => string;
};
const LocaleCtx = createContext<Ctx | null>(null);

function isLocale(v: string | null): v is Locale {
  return v === "en" || v === "km" || v === "ko";
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    if (typeof window === "undefined") return "en";
    const saved = window.localStorage.getItem(KEY);
    return isLocale(saved) ? saved : "en";
  });

  useEffect(() => {
    if (typeof window !== "undefined") window.localStorage.setItem(KEY, locale);
    document.documentElement.lang = locale;
  }, [locale]);

  const t = useCallback(
    (key: string, vars?: Record<string, string | number>) => {
      const dict = DICTS[locale] ?? DICTS.en;
      let s = dict[key] ?? DICTS.en[key] ?? key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          s = s.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
        }
      }
      return s;
    },
    [locale],
  );

  const value = useMemo<Ctx>(() => ({ locale, setLocale: setLocaleState, t }), [locale, t]);
  return createElement(LocaleCtx.Provider, { value }, children);
}

export function useLocale() {
  const v = useContext(LocaleCtx);
  if (!v) throw new Error("useLocale must be used within LocaleProvider");
  return v;
}

export function useT() {
  return useLocale().t;
}
