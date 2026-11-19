/** Validation display helpers (ported from the legacy Streamlit UI). */

export type Translate = (key: string, opts?: Record<string, unknown>) => string;

/** English defaults used when no translator is provided (unit tests). */
export const VALIDATION_BLOCK_LABELS: Record<string, string> = {
  c1_question: 'Question phrasing',
  c2_outputs: 'Answer phrasing',
  c2_options: 'Answer options',
  c3_outputs: 'Correct-answer consistency',
  c4_logic: 'Logical consistency',
  c5_phrase: 'Phrasing correctness',
};

const VALIDATION_BLOCK_KEYS: Record<string, string> = {
  c1_question: 'results:valBlockC1',
  c2_outputs: 'results:valBlockC2Outputs',
  c2_options: 'results:valBlockC2Options',
  c3_outputs: 'results:valBlockC3',
  c4_logic: 'results:valBlockC4',
  c5_phrase: 'results:valBlockC5',
};

export const VALIDATION_BLOCK_ORDER = [
  'c1_question',
  'c2_outputs',
  'c2_options',
  'c3_outputs',
  'c4_logic',
  'c5_phrase',
] as const;

export function blockLabel(blockKey: string, t?: Translate): string {
  const i18nKey = VALIDATION_BLOCK_KEYS[blockKey];
  if (t && i18nKey) return t(i18nKey);
  return VALIDATION_BLOCK_LABELS[blockKey] ?? blockKey;
}

function parsePythonLiteral(raw: string): unknown {
  let i = 0;
  const s = raw.trim();

  function skipWs() {
    while (i < s.length && /\s/.test(s[i])) i++;
  }

  function parseValue(): unknown {
    skipWs();
    if (i >= s.length) return null;
    const ch = s[i];

    if (ch === '{') {
      i++;
      const obj: Record<string, unknown> = {};
      skipWs();
      if (s[i] === '}') {
        i++;
        return obj;
      }
      while (i < s.length) {
        skipWs();
        const key = parseValue();
        skipWs();
        if (s[i] !== ':') break;
        i++;
        obj[String(key)] = parseValue();
        skipWs();
        if (s[i] === ',') {
          i++;
          continue;
        }
        if (s[i] === '}') {
          i++;
          break;
        }
      }
      return obj;
    }

    if (ch === '[') {
      i++;
      const arr: unknown[] = [];
      skipWs();
      if (s[i] === ']') {
        i++;
        return arr;
      }
      while (i < s.length) {
        arr.push(parseValue());
        skipWs();
        if (s[i] === ',') {
          i++;
          continue;
        }
        if (s[i] === ']') {
          i++;
          break;
        }
      }
      return arr;
    }

    if (ch === '"' || ch === "'") {
      const quote = ch;
      i++;
      let out = '';
      while (i < s.length) {
        if (s[i] === '\\' && i + 1 < s.length) {
          out += s[i + 1];
          i += 2;
          continue;
        }
        if (s[i] === quote) {
          i++;
          break;
        }
        out += s[i];
        i++;
      }
      return out;
    }

    if (/[-\d.]/.test(ch)) {
      const start = i;
      while (i < s.length && /[-\d.eE+]/.test(s[i])) i++;
      const num = Number(s.slice(start, i));
      return Number.isNaN(num) ? s.slice(start, i) : num;
    }

    const start = i;
    while (i < s.length && /[a-zA-Z_]/.test(s[i])) i++;
    const word = s.slice(start, i);
    if (word === 'True') return true;
    if (word === 'False') return false;
    if (word === 'None') return null;
    return word;
  }

  try {
    return parseValue();
  } catch {
    return null;
  }
}

function tryParseLiteral(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    // fall through
  }
  try {
    const normalized = raw
      .replace(/\bTrue\b/g, 'true')
      .replace(/\bFalse\b/g, 'false')
      .replace(/\bNone\b/g, 'null');
    return JSON.parse(normalized);
  } catch {
    // fall through
  }
  return parsePythonLiteral(raw);
}

export function parseValidationDetails(raw: unknown): Record<string, unknown> {
  if (raw == null) return {};
  if (typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === 'string') {
    const s = raw.trim();
    if (!s) return {};
    const parsed = tryParseLiteral(s);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  }
  return {};
}

export function parseValidationJustifications(raw: unknown): Record<string, string[]> {
  if (raw == null) return {};
  if (typeof raw === 'object' && !Array.isArray(raw)) {
    const result: Record<string, string[]> = {};
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      result[k] = Array.isArray(v) ? v.map(String) : [];
    }
    return result;
  }
  if (typeof raw === 'string') {
    const s = raw.trim();
    if (!s) return {};
    const parsed = tryParseLiteral(s);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const result: Record<string, string[]> = {};
      for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
        result[k] = Array.isArray(v) ? v.map(String) : [];
      }
      return result;
    }
  }
  return {};
}

export const VALIDATION_BLOCK_META_KEYS = new Set([
  '__block_weighted__',
  '__block_max__',
]);
