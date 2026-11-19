import { describe, expect, it, vi } from 'vitest';
import { shuffleQuestions } from './shuffleOptions';
import type { Question } from '../types/documents';

function makeQuestion(overrides: Partial<Question> = {}): Question {
  return {
    chunk_id: 1,
    question_type: 'one',
    task: 'Sample?',
    options: {
      option_1: 'Alpha',
      option_2: 'Beta',
      option_3: 'Gamma',
    },
    correct_answer: '2',
    provocativeness: '2',
    ...overrides,
  };
}

describe('shuffleQuestions', () => {
  it('does not mutate the original array', () => {
    const original = [makeQuestion()];
    const before = structuredClone(original);
    shuffleQuestions(original);
    expect(original).toEqual(before);
  });

  it('preserves correct answer after deterministic shuffle', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0);
    const [shuffled] = shuffleQuestions([makeQuestion()]);
    const opts = shuffled.options as Record<string, string>;
    const correctIdx = String(shuffled.correct_answer);
    expect(opts[`option_${correctIdx}`]).toBe('Beta');
    vi.restoreAllMocks();
  });

  it('handles multi correct answers with comma-separated indices', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5);
    const q = makeQuestion({
      question_type: 'multi',
      correct_answer: '1,3',
      options: {
        option_1: 'A',
        option_2: 'B',
        option_3: 'C',
      },
    });
    const [shuffled] = shuffleQuestions([q]);
    const opts = shuffled.options as Record<string, string>;
    const indices = String(shuffled.correct_answer)
      .split(',')
      .map((x) => `option_${x.trim()}`);
    const values = indices.map((k) => opts[k]);
    expect(values).toContain('A');
    expect(values).toContain('C');
    vi.restoreAllMocks();
  });

  it('skips questions with string options', () => {
    const q = makeQuestion({ options: 'option_1: A' });
    const [shuffled] = shuffleQuestions([q]);
    expect(shuffled.options).toBe('option_1: A');
  });
});
