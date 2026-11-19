import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

/** Files/paths skipped entirely. */
const SKIP_FILES = [
  /\/docs\/DocsPage\.tsx$/,
  /\/shared\/ui\/charts\//,
  /\.test\.tsx?$/,
  /\.guard\.test\.ts$/,
];

/** Attribute names checked for English literals. */
const ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];

/**
 * Heuristic: a string looks like user-facing English if it has Latin words
 * and is a phrase (contains a space) or a Title-Case word ≥ 5 letters.
 */
function looksLikeEnglishUi(s: string): boolean {
  const t = s.trim();
  if (t.length < 3) return false;
  if (/[{}`]/.test(t)) return false; // JSX/template fragments
  if (/^[\d\s.,:%+/−–—×·•…\-]+$/.test(t)) return false;
  if (/^(https?:|mailto:|\/|#)/i.test(t)) return false;
  if (/^(GenA|EN|RU|CSV|PDF|DOCX|JSON|GIFT|OK|ID)$/i.test(t)) return false;
  if (!/[A-Za-z]/.test(t)) return false;
  if (/\s/.test(t)) return /[A-Za-z]{2,}/.test(t);
  return /^[A-Z][a-z]{4,}/.test(t);
}

function walkTsx(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name === 'dist') continue;
      walkTsx(full, out);
    } else if (entry.name.endsWith('.tsx')) {
      out.push(full);
    }
  }
  return out;
}

function findLiterals(source: string): Array<{ kind: string; value: string; line: number }> {
  const hits: Array<{ kind: string; value: string; line: number }> = [];
  const lines = source.split('\n');

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const lineNo = i + 1;

    if (/^\s*(import|export)\b/.test(line)) continue;
    if (/^\s*\/\//.test(line)) continue;

    // JSX text between tags. Skip `=>` arrows and require a real tag close.
    for (const m of line.matchAll(/(?<!=)>([^<{][^<>]*?)</g)) {
      const value = m[1].trim();
      if (looksLikeEnglishUi(value)) hits.push({ kind: 'jsx-text', value, line: lineNo });
    }

    for (const attr of ATTRS) {
      const re = new RegExp(`${attr}=["']([^"']+)["']`, 'g');
      for (const m of line.matchAll(re)) {
        const value = m[1].trim();
        if (looksLikeEnglishUi(value)) hits.push({ kind: attr, value, line: lineNo });
      }
    }
  }

  return hits;
}

describe('i18n literal guard', () => {
  it('flags hardcoded English UI strings in TSX', () => {
    const files = walkTsx(ROOT).filter((f) => !SKIP_FILES.some((re) => re.test(f)));
    const offenders: string[] = [];

    for (const file of files) {
      const src = fs.readFileSync(file, 'utf8');
      for (const hit of findLiterals(src)) {
        const rel = path.relative(ROOT, file);
        offenders.push(`${rel}:${hit.line} [${hit.kind}] "${hit.value}"`);
      }
    }

    expect(offenders, offenders.join('\n')).toEqual([]);
  });
});
