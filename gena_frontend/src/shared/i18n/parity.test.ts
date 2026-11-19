import { describe, expect, it } from 'vitest';
import { SUPPORTED_LOCALES } from './config';

type LocaleTree = Record<string, unknown>;

const localeModules = import.meta.glob('./locales/*/*.json', { eager: true }) as Record<
  string,
  { default?: LocaleTree } | LocaleTree
>;

function unwrap(mod: { default?: LocaleTree } | LocaleTree): LocaleTree {
  if (mod && typeof mod === 'object' && 'default' in mod && mod.default) {
    return mod.default as LocaleTree;
  }
  return (mod ?? {}) as LocaleTree;
}

function flattenKeys(obj: LocaleTree, prefix = ''): string[] {
  const keys: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v != null && typeof v === 'object' && !Array.isArray(v)) {
      keys.push(...flattenKeys(v as LocaleTree, path));
    } else {
      keys.push(path);
    }
  }
  return keys;
}

/**
 * i18next v4 JSON plural suffixes; forms differ by language
 * (en: one/other, ru: one/few/many/other).
 */
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;

function normalizePluralKeys(keys: string[]): string[] {
  return Array.from(
    new Set(keys.map((k) => k.replace(PLURAL_SUFFIX, '_plural'))),
  ).sort();
}

function collectByLocale(): Record<string, Record<string, LocaleTree>> {
  const out: Record<string, Record<string, LocaleTree>> = {};
  for (const [path, mod] of Object.entries(localeModules)) {
    const match = path.match(/\.\/locales\/([^/]+)\/([^/]+)\.json$/);
    if (!match) continue;
    const [, locale, ns] = match;
    if (!out[locale]) out[locale] = {};
    out[locale][ns] = unwrap(mod);
  }
  return out;
}

const byLocale = collectByLocale();
const referenceLocale = 'en';

/** Locales expected to use non-Latin script for most UI strings. */
const NON_LATIN_LOCALES = new Set(['ru']);

function looksLatinOnly(value: string): boolean {
  // Ignore short codes, interpolations-only, punctuation, and brand tokens.
  const stripped = value
    .replace(/\{\{[^}]+\}\}/g, '')
    .replace(/[0-9%.,:;!?()«»""''—–\-/\s]+/g, '')
    .replace(/\b(GenA|EN|RU|CSV|PDF|DOCX|JSON|GIFT|Moodle|Word|Excel|LLM|API)\b/gi, '');
  if (stripped.length < 4) return false;
  return /^[A-Za-z]+$/.test(stripped) && /[a-z]{3,}/i.test(stripped);
}

describe('i18n locale parity', () => {
  it('has locale folders for every SUPPORTED_LOCALES entry', () => {
    for (const locale of SUPPORTED_LOCALES) {
      expect(byLocale[locale], `missing locale folder: ${locale}`).toBeTruthy();
    }
  });

  it('has identical namespaces across all supported locales', () => {
    const refNs = Object.keys(byLocale[referenceLocale] ?? {}).sort();
    for (const locale of SUPPORTED_LOCALES) {
      expect(Object.keys(byLocale[locale] ?? {}).sort()).toEqual(refNs);
    }
  });

  it('has identical keys within each namespace across locales', () => {
    const ref = byLocale[referenceLocale];
    for (const ns of Object.keys(ref)) {
      const refKeys = normalizePluralKeys(flattenKeys(ref[ns]));
      for (const locale of SUPPORTED_LOCALES) {
        const keys = normalizePluralKeys(flattenKeys(byLocale[locale][ns]));
        expect(keys, `${locale}:${ns} key mismatch`).toEqual(refKeys);
      }
    }
  });

  it('uses i18next v4 plural suffixes, not the legacy _plural form', () => {
    const legacy: string[] = [];
    for (const locale of SUPPORTED_LOCALES) {
      for (const [ns, tree] of Object.entries(byLocale[locale])) {
        for (const key of flattenKeys(tree)) {
          if (key.endsWith('_plural')) legacy.push(`${locale}:${ns}.${key}`);
        }
      }
    }
    expect(legacy).toEqual([]);
  });

  it('has no empty string values', () => {
    const empties: string[] = [];
    for (const locale of SUPPORTED_LOCALES) {
      for (const [ns, tree] of Object.entries(byLocale[locale])) {
        for (const key of flattenKeys(tree)) {
          const parts = key.split('.');
          let cur: unknown = tree;
          for (const p of parts) cur = (cur as LocaleTree)[p];
          if (cur === '') empties.push(`${locale}:${ns}.${key}`);
        }
      }
    }
    expect(empties).toEqual([]);
  });

  it('non-Latin locales are not identical to English for leaf strings', () => {
    const ref = byLocale[referenceLocale];
    const offenders: string[] = [];
    // Allow-list: brand codes, abbreviations, shared technical tokens.
    const allowExact = new Set([
      'EN',
      'RU',
      'GenA',
      'n/a',
      '—',
      '{{page}} — GenA',
    ]);

    for (const locale of SUPPORTED_LOCALES) {
      if (!NON_LATIN_LOCALES.has(locale)) continue;
      for (const [ns, tree] of Object.entries(byLocale[locale])) {
        const refTree = ref[ns];
        for (const key of flattenKeys(tree)) {
          const parts = key.split('.');
          let cur: unknown = tree;
          let refCur: unknown = refTree;
          for (const p of parts) {
            cur = (cur as LocaleTree)?.[p];
            refCur = (refCur as LocaleTree)?.[p];
          }
          if (typeof cur !== 'string' || typeof refCur !== 'string') continue;
          if (allowExact.has(cur)) continue;
          if (cur === refCur && looksLatinOnly(cur)) {
            offenders.push(`${locale}:${ns}.${key} = "${cur}"`);
          }
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
