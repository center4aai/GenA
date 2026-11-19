import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import i18n from './index';
import { SUPPORTED_LOCALES } from './config';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(HERE, '../..');
const LOCALES = path.join(HERE, 'locales');

type LocaleTree = Record<string, unknown>;

function readNs(locale: string, ns: string): LocaleTree {
  return JSON.parse(fs.readFileSync(path.join(LOCALES, locale, `${ns}.json`), 'utf8'));
}

function lookup(tree: LocaleTree, dottedKey: string): unknown {
  let cur: unknown = tree;
  for (const part of dottedKey.split('.')) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = (cur as LocaleTree)[part];
  }
  return cur;
}

/** Placeholders referenced by a template string, e.g. {{name}} → "name". */
function placeholders(template: string): Set<string> {
  return new Set(Array.from(template.matchAll(/\{\{\s*([\w.]+)\s*\}\}/g), (m) => m[1]));
}

/** Plural variants of a key that i18next may resolve to. */
function pluralVariants(key: string): string[] {
  return ['', '_zero', '_one', '_two', '_few', '_many', '_other'].map((s) => `${key}${s}`);
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'dist') continue;
      walk(full, out);
    } else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Match single-line `t('ns:key', { a: ..., b: ... })` calls with a flat object
 * literal. Nested braces or spreads are skipped: the goal is a cheap guard for
 * the common case, not a parser.
 */
const CALL_RE = /\bt\(\s*'([a-zA-Z]+:[\w.]+)'\s*,\s*\{([^{}]*)\}\s*\)/g;

/** Property names of a flat object literal, including shorthand (`{ n, max: 2 }`). */
function argNames(objectBody: string): string[] {
  const names: string[] = [];
  for (const part of objectBody.split(',')) {
    const name = part.split(':')[0].trim();
    if (/^[A-Za-z_$][\w$]*$/.test(name)) names.push(name);
  }
  return names;
}

describe('i18n interpolation guard', () => {
  it('every t() call supplies the variables its template needs', () => {
    const en = new Map<string, LocaleTree>();
    const problems: string[] = [];

    for (const file of walk(SRC)) {
      const src = fs.readFileSync(file, 'utf8');
      for (const m of src.matchAll(CALL_RE)) {
        const [full, nsKey, body] = m;
        const [ns, key] = nsKey.split(':');
        if (!en.has(ns)) {
          try {
            en.set(ns, readNs('en', ns));
          } catch {
            problems.push(`${path.relative(SRC, file)}: unknown namespace "${ns}"`);
            continue;
          }
        }
        const tree = en.get(ns)!;
        const template = pluralVariants(key)
          .map((k) => lookup(tree, k))
          .find((v) => typeof v === 'string') as string | undefined;
        if (template === undefined) {
          problems.push(`${path.relative(SRC, file)}: missing key "${nsKey}"`);
          continue;
        }
        const provided = new Set(argNames(body));
        // Skip calls whose args we cannot read (spread, nested objects).
        if (!provided.size && body.trim()) continue;
        for (const need of placeholders(template)) {
          if (!provided.has(need)) {
            problems.push(
              `${path.relative(SRC, file)}: ${nsKey} needs {{${need}}}, got {${[...provided].join(', ')}} in ${full.trim()}`,
            );
          }
        }
      }
    }

    expect(problems, problems.join('\n')).toEqual([]);
  });

  it('resolves plural keys per locale without leaking placeholders', async () => {
    const pluralKeys = [
      'results:unsaved',
      'results:excludedCount',
      'results:revalRetryN',
      'progress:failedTasksSummary',
      'common:datasets',
      'common:queues',
      'common:tasks',
      'common:items',
    ];
    const previous = i18n.language;

    for (const locale of SUPPORTED_LOCALES) {
      await i18n.changeLanguage(locale);
      for (const key of pluralKeys) {
        for (const count of [1, 2, 5, 21]) {
          const out = i18n.t(key, { count });
          expect(out, `${locale} ${key} count=${count}`).not.toContain('{{');
          expect(out, `${locale} ${key} count=${count}`).toContain(String(count));
        }
      }
      // English and Russian must differ in plural wording for 1 vs 5.
      expect(i18n.t('results:unsaved', { count: 1 })).not.toBe(
        i18n.t('results:unsaved', { count: 5 }),
      );
    }

    await i18n.changeLanguage(previous);
  });
});
