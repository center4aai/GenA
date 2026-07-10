import type { Question } from '../types/documents';

/** Shuffle option values while preserving correct answer indices (dynamic_implementation_controller.shuffle_questions). */
export function shuffleQuestions(questions: Question[]): Question[] {
  const copy = structuredClone(questions);

  for (const question of copy) {
    const options = question.options;
    if (typeof options !== 'object' || options == null || Array.isArray(options)) continue;
    if (Object.keys(options).length === 0) continue;

    const correctAnswerKey = question.correct_answer;
    if (!correctAnswerKey) continue;

    let indices: number[];
    try {
      indices = [parseInt(String(correctAnswerKey), 10)];
    } catch {
      indices = [];
    }
    if (Number.isNaN(indices[0])) {
      try {
        indices = String(correctAnswerKey)
          .split(',')
          .map((x) => parseInt(x.trim(), 10))
          .filter((n) => !Number.isNaN(n));
      } catch {
        continue;
      }
    }

    const originalCorrectValues = new Set<string>();
    for (const idx of indices) {
      const optionKey = `option_${idx}`;
      if (optionKey in options) {
        originalCorrectValues.add(String((options as Record<string, string>)[optionKey]));
      }
    }

    if (originalCorrectValues.size === 0) continue;

    const values = Object.values(options as Record<string, string>);
    for (let i = values.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [values[i], values[j]] = [values[j], values[i]];
    }

    const keys = Object.keys(options as Record<string, string>);
    const newOptions: Record<string, string> = {};
    keys.forEach((key, i) => {
      newOptions[key] = values[i];
    });
    question.options = newOptions;

    const newIndices: number[] = [];
    for (const [key, value] of Object.entries(newOptions)) {
      if (originalCorrectValues.has(value)) {
        const num = parseInt(key.split('_')[1] ?? '', 10);
        if (!Number.isNaN(num)) newIndices.push(num);
      }
    }
    newIndices.sort((a, b) => a - b);

    question.correct_answer =
      newIndices.length === 1 ? String(newIndices[0]) : newIndices.join(',');
  }

  return copy;
}
