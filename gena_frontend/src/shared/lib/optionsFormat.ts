/** Options formatting helpers (dataset_editor.py). */

export function normOptions(opts: unknown): string {
  if (opts == null) return '';
  if (typeof opts === 'object' && !Array.isArray(opts)) {
    const parts = Object.entries(opts as Record<string, unknown>)
      .filter(([, v]) => v != null && v !== 'None' && v !== '')
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k}: ${v}`);
    return parts.join('\n');
  }
  return String(opts);
}

export function asOptionsText(opts: unknown): string {
  return normOptions(opts);
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

export function coerceOptionsLike(original: unknown, edited: unknown): unknown {
  if (typeof original === 'object' && original !== null && !Array.isArray(original)) {
    if (typeof edited === 'object' && edited !== null && !Array.isArray(edited)) {
      return edited;
    }
    return parseOptionsText(String(edited ?? ''));
  }
  return String(edited ?? '');
}

export function optionsToNumberedList(opts: unknown): string {
  if (typeof opts === 'object' && opts !== null && !Array.isArray(opts)) {
    const entries = Object.entries(opts as Record<string, string>)
      .filter(([, v]) => v != null && v !== 'None')
      .map(([k, v]) => {
        const num = k.replace('option_', '');
        return [parseInt(num, 10), v] as [number, string];
      })
      .sort(([a], [b]) => a - b);
    if (!entries.length) return 'No options provided';
    return entries.map(([i, text]) => `${i}. ${text}`).join('\n');
  }
  return String(opts ?? 'No options provided');
}

export function optionsDictFromGenerated(gq: Record<string, unknown>): Record<string, string> {
  const dict: Record<string, string> = {};
  for (const [k, v] of Object.entries(gq)) {
    if (k.startsWith('option_') && v != null && v !== 'None') {
      dict[k] = String(v);
    }
  }
  return dict;
}
