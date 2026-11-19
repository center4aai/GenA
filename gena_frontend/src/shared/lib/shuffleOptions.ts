import type { Question } from '../types/documents';

function parseCorrectIndices(correctAnswerKey: unknown): number[] {
  const raw = String(correctAnswerKey ?? '').trim();
  if (!raw) return [];
  try {
    const single = parseInt(raw, 10);
    if (!Number.isNaN(single) && !raw.includes(',')) return [single];
  } catch {
    // fall through to comma-separated parse
  }
  if (raw.includes(',')) {
    return raw
      .split(',')
      .map((x) => parseInt(x.trim(), 10))
      .filter((n) => !Number.isNaN(n));
  }
  return [];
}

/** Shuffle option values while preserving correct answer indices (dynamic_implementation_controller.shuffle_questions). */
export function shuffleQuestions(questions: Question[]): Question[] {
  const copy = structuredClone(questions);

  for (const question of copy) {
    const options = question.options;
    if (typeof options !== 'object' || options == null || Array.isArray(options)) continue;
    if (Object.keys(options).length === 0) continue;

    const correctAnswerKey = question.correct_answer;
    if (!correctAnswerKey) continue;

    const indices = parseCorrectIndices(correctAnswerKey);
    if (!indices.length) continue;

    const opts = options as Record<string, string>;
    const originalCorrectValues = new Set<string>();
    for (const idx of indices) {
      const optionKey = `option_${idx}`;
      if (optionKey in opts) {
        originalCorrectValues.add(opts[optionKey]);
      }
    }
    if (originalCorrectValues.size === 0) continue;

    const keys = Object.keys(opts);
    const values = keys.map((k) => opts[k]);

    for (let i = values.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [values[i], values[j]] = [values[j], values[i]];
    }

    const newOptions: Record<string, string> = {};
    keys.forEach((key, i) => {
      newOptions[key] = values[i];
    });
    question.options = newOptions;

    const newIndices: number[] = [];
    for (const [key, value] of Object.entries(newOptions)) {
      if (!originalCorrectValues.has(value)) continue;
      const parts = key.split('_');
      if (parts.length < 2) continue;
      const num = parseInt(parts[1], 10);
      if (!Number.isNaN(num)) newIndices.push(num);
    }
    newIndices.sort((a, b) => a - b);

    question.correct_answer =
      newIndices.length === 1 ? String(newIndices[0]) : newIndices.join(',');
  }

  return copy;
}
