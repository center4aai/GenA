/** Port of gena_web/gena/validation_display.py */

export const MAX_REFINE_ATTEMPTS = 2;

export const VALIDATION_BLOCK_LABELS: Record<string, string> = {
  c1_question: 'Question phrasing',
  c2_outputs: 'Answer phrasing',
  c2_options: 'Answer options',
  c3_outputs: 'Correct-answer consistency',
  c4_logic: 'Logical consistency',
  c5_phrase: 'Phrasing correctness',
};

export function blockLabel(blockKey: string): string {
  return VALIDATION_BLOCK_LABELS[blockKey] ?? blockKey;
}

function tryParseLiteral(raw: string): unknown {
  try {
    // Safe-ish parse for Python dict/list literals stored as strings
    const normalized = raw
      .replace(/\bTrue\b/g, 'true')
      .replace(/\bFalse\b/g, 'false')
      .replace(/\bNone\b/g, 'null')
      .replace(/'/g, '"');
    return JSON.parse(normalized);
  } catch {
    return null;
  }
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

export function formatValidationBreakdown(
  byBlock: Record<string, unknown>,
  justifications: Record<string, string[]> = {},
): string[] {
  const lines: string[] = [];
  for (const [blockKey, scores] of Object.entries(byBlock)) {
    const label = blockLabel(blockKey);
    if (!Array.isArray(scores)) {
      lines.push(`${label}: ${String(scores)}`);
      continue;
    }
    let total = 0;
    try {
      total = scores.reduce((sum: number, x) => sum + Number(x), 0);
    } catch {
      total = scores.length ? 1 : 0;
    }
    lines.push(`${label}: ${total}/${scores.length}`);
    const blockJusts = justifications[blockKey] ?? [];
    scores.forEach((sc, i) => {
      if (Number(sc) === 0 && i < blockJusts.length) {
        const j = (blockJusts[i] ?? '').trim();
        if (j) lines.push(`  (${i + 1}) ${j}`);
      }
    });
  }
  return lines.length ? lines : ['No data'];
}

export function formatRetryLine(retryCount: unknown): string {
  let n = 0;
  try {
    n = parseInt(String(retryCount), 10);
    if (Number.isNaN(n)) n = 0;
  } catch {
    n = 0;
  }
  return `Refinement attempts: ${n}/${MAX_REFINE_ATTEMPTS}`;
}
