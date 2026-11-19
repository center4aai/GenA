/** Options formatting helpers (dataset_editor.py). */

/** Sorted option entries as `{ key, index, text }` for UI lists. */
export function optionsEntries(opts: unknown): Array<{ key: string; index: number; text: string }> {
  if (typeof opts !== 'object' || opts == null || Array.isArray(opts)) return [];
  return Object.entries(opts as Record<string, unknown>)
    .filter(([, v]) => v != null && v !== 'None' && v !== '')
    .map(([k, v]) => {
      const index = parseInt(k.replace(/^option_/, ''), 10);
      return { key: k, index: Number.isNaN(index) ? 0 : index, text: String(v) };
    })
    .sort((a, b) => a.index - b.index || a.key.localeCompare(b.key));
}

/** Whether `correctAnswer` marks this option index/key as correct. */
export function isCorrectOption(correctAnswer: unknown, optionKey: string, optionIndex: number): boolean {
  const raw = String(correctAnswer ?? '').trim();
  if (!raw) return false;
  const tokens = raw.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
  return tokens.some((tok) => {
    if (tok === optionKey) return true;
    if (tok === String(optionIndex)) return true;
    if (tok === `option_${optionIndex}`) return true;
    return false;
  });
}

export function normOptions(opts: unknown): string {
  if (opts == null) return '';
  if (typeof opts === 'object' && !Array.isArray(opts)) {
    const parts = Object.entries(opts as Record<string, unknown>)
      .filter(([, v]) => v != null && v !== 'None' && v !== '')
      .sort(([a], [b]) => {
        const na = parseInt(a.replace(/^option_/, ''), 10);
        const nb = parseInt(b.replace(/^option_/, ''), 10);
        if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
        return a.localeCompare(b);
      })
      .map(([k, v]) => `${k}: ${v}`);
    return parts.join('\n');
  }
  return String(opts);
}

export function parseOptionsText(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  if (!text) return result;
  for (const line of text.split('\n')) {
    if (line.includes(':')) {
      const [k, ...rest] = line.split(':');
      const key = k.trim();
      const value = rest.join(':').trim();
      if (key) result[key] = value;
    }
  }
  return result;
}

function isOptionsRecord(value: unknown): value is Record<string, string> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Keep the storage shape of `original` for an edit: a record stays a record, a
 * legacy `option_N: text` string stays a string. A question without options
 * (an open one being turned into a choice) takes whatever shape the edit has.
 */
export function coerceOptionsLike(
  original: unknown,
  edited: unknown,
): string | Record<string, string> {
  if (isOptionsRecord(original)) {
    return isOptionsRecord(edited) ? edited : parseOptionsText(String(edited ?? ''));
  }
  if (isOptionsRecord(edited)) {
    return original == null || String(original).trim() === '' ? edited : normOptions(edited);
  }
  return String(edited ?? '');
}

/** Resolve correct_answer index(es) to option text for display. */
export function resolveAnswerText(opts: unknown, correctAnswer: unknown): string {
  const raw = String(correctAnswer ?? '').trim();
  if (!raw) return '';
  if (typeof opts !== 'object' || opts === null || Array.isArray(opts)) return raw;
  const dict = opts as Record<string, string>;
  const indices = raw.split(/[,;\s]+/).map((s) => s.trim()).filter(Boolean);
  const texts = indices.map((idx) => {
    const key = idx.startsWith('option_') ? idx : `option_${idx}`;
    const byKey = dict[key];
    if (byKey) return byKey;
    const n = parseInt(idx, 10);
    if (!Number.isNaN(n) && dict[`option_${n}`]) return dict[`option_${n}`];
    return idx;
  });
  return texts.join(', ');
}

