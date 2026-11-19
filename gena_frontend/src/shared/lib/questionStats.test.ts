import { describe, expect, it } from 'vitest';
import { computeQuestionStats } from './questionStats';
import type { Question } from '../types/documents';

const QS: Question[] = [
  { question_type: 'one', provocativeness: '1', difficulty: '1', validation_passed: 'True' },
  { question_type: 'single', provocativeness: '1', difficulty: '2', validation_passed: 'False', excluded_from_export: true },
  { question_type: 'multi', provocativeness: '2', difficulty: null, validation_passed: true },
  { question_type: 'open', sensitivity_level: 3, difficulty_level: '3', validation_score: '15/16', validation_threshold: '14.0' },
  { question_type: 'essay', provocativeness: '', difficulty: '—' },
];

describe('computeQuestionStats', () => {
  const stats = computeQuestionStats(QS);

  it('counts types through their aliases', () => {
    expect(stats.total).toBe(5);
    expect(stats.types).toEqual({ one: 2, multi: 1, open: 1 });
  });

  it('counts levels from canonical and legacy fields, skipping blanks', () => {
    expect(stats.sensitivity).toEqual({ '1': 2, '2': 1, '3': 1 });
    expect(stats.difficulty).toEqual({ '1': 1, '2': 1, '3': 1 });
  });

  it('derives the pass rate only from questions with a verdict', () => {
    expect(stats.withVerdict).toBe(4);
    expect(stats.passed).toBe(3);
    expect(stats.passRate).toBe(75);
    expect(computeQuestionStats([{ task: 'x' }]).passRate).toBeNull();
  });

  it('counts exclusions', () => {
    expect(stats.excluded).toBe(1);
  });
});
