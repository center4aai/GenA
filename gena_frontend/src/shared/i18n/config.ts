export const SUPPORTED_LOCALES = ['en', 'ru'] as const;

export type AppLocale = (typeof SUPPORTED_LOCALES)[number];

export const LOCALE_STORAGE_KEY = 'gena_locale';

export function isAppLocale(value: unknown): value is AppLocale {
  return typeof value === 'string' && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

/** Resolution order: localStorage → VITE_DEFAULT_LOCALE → en */
export function resolveDefaultLocale(): AppLocale {
  if (typeof window !== 'undefined') {
    const stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    if (isAppLocale(stored)) return stored;
  }
  const fromEnv = import.meta.env.VITE_DEFAULT_LOCALE;
  if (isAppLocale(fromEnv)) return fromEnv;
  return 'en';
}

export function persistLocale(locale: AppLocale) {
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  }
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale;
  }
}
