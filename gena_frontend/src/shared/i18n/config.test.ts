import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  isAppLocale,
  resolveDefaultLocale,
  LOCALE_STORAGE_KEY,
  SUPPORTED_LOCALES,
} from './config';

describe('i18n config', () => {
  afterEach(() => {
    localStorage.removeItem(LOCALE_STORAGE_KEY);
    vi.unstubAllEnvs();
  });

  it('accepts only supported locales', () => {
    for (const locale of SUPPORTED_LOCALES) {
      expect(isAppLocale(locale)).toBe(true);
    }
    expect(isAppLocale('de')).toBe(false);
    expect(isAppLocale('')).toBe(false);
    expect(isAppLocale(null)).toBe(false);
  });

  it('prefers localStorage over env default', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'ru');
    vi.stubEnv('VITE_DEFAULT_LOCALE', 'en');
    expect(resolveDefaultLocale()).toBe('ru');
  });

  it('falls back to en when unset', () => {
    vi.stubEnv('VITE_DEFAULT_LOCALE', '');
    expect(resolveDefaultLocale()).toBe('en');
  });
});
