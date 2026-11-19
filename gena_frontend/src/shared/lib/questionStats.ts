import { isValidationPassed } from '@/shared/lib/progress';
import { isValidationCurrent } from '@/shared/lib/revalidation';
import { isExcludedFromExport } from '@/shared/lib/questionEdits';
import {
  difficultyValue,
  normalizeQuestionType,
  sensitivityValue,
  type QuestionKind,
} from '@/shared/lib/questionFields';
import type { Question } from '@/shared/types/documents';

export type Level = '1' | '2' | '3';
export const LEVELS: Level[] = ['1', '2', '3'];
export const QUESTION_KINDS: QuestionKind[] = ['one', 'multi', 'open'];

export interface QuestionStats {
  total: number;
  types: Record<QuestionKind, number>;
  sensitivity: Record<Level, number>;
  difficulty: Record<Level, number>;
  /** Questions carrying a verdict, and how many of them passed. */
  withVerdict: number;
  passed: number;
  /** 0–100, null when no question has a verdict yet. */
  passRate: number | null;
  excluded: number;
}

function isLevel(value: string): value is Level {
  return value === '1' || value === '2' || value === '3';
}

/** Per-version figures for the card: counts by type and level, pass rate, exclusions. */
export function computeQuestionStats(questions: Question[]): QuestionStats {
  const stats: QuestionStats = {
    total: questions.length,
    types: { one: 0, multi: 0, open: 0 },
    sensitivity: { '1': 0, '2': 0, '3': 0 },
    difficulty: { '1': 0, '2': 0, '3': 0 },
    withVerdict: 0,
    passed: 0,
    passRate: null,
    excluded: 0,
  };
  for (const q of questions) {
    const kind = normalizeQuestionType(q.question_type);
    if (kind) stats.types[kind] += 1;
    const sens = sensitivityValue(q);
    if (isLevel(sens)) stats.sensitivity[sens] += 1;
    const diff = difficultyValue(q);
    if (isLevel(diff)) stats.difficulty[diff] += 1;
    // A stale verdict (edited or rephrased since) is not counted either way.
    const verdict = isValidationCurrent(q) ? isValidationPassed(q) : null;
    if (verdict !== null) {
      stats.withVerdict += 1;
      if (verdict) stats.passed += 1;
    }
    if (isExcludedFromExport(q)) stats.excluded += 1;
  }
  stats.passRate = stats.withVerdict ? (stats.passed / stats.withVerdict) * 100 : null;
  return stats;
}
