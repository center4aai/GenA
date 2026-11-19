import { describe, expect, it } from 'vitest';
import {
  applyQuestionEdits,
  difficultyValue,
  ensureQids,
  hasRealQuestionId,
  normalizeQuestionType,
  originalDifficultyValue,
  originalSensitivityValue,
  sensitivityValue,
} from './questionFields';

describe('ensureQids / hasRealQuestionId', () => {
  it('fabricates ids only for questions that lack one', () => {
    const [a, b] = ensureQids('ds1', [{ question_id: 'uuid-1' }, { task: 'no id' }]);
    expect(a.question_id).toBe('uuid-1');
    expect(b.question_id).toBe('ds1:00001');
  });

  it('tells server ids from synthetic ones', () => {
    expect(hasRealQuestionId({ question_id: '3f2a9c1e-5d3b-4c2a-9b1e-2d4f6a8c0e12' })).toBe(true);
    expect(hasRealQuestionId({ question_id: 'ds1:00001' })).toBe(false);
    expect(hasRealQuestionId({})).toBe(false);
    expect(hasRealQuestionId({ question_id: '' })).toBe(false);
  });
});

describe('level accessors', () => {
  it('read the canonical field and fall back to the legacy alias', () => {
    expect(sensitivityValue({ provocativeness: '2' })).toBe('2');
    expect(sensitivityValue({ sensitivity_level: 3 })).toBe('3');
    expect(sensitivityValue({ provocativeness: '' })).toBe('');
    expect(difficultyValue({ difficulty: null, difficulty_level: '1' })).toBe('1');
    expect(difficultyValue({ difficulty: '—' })).toBe('');
  });

  it('prefer the kept original and fall back to the live level', () => {
    expect(originalSensitivityValue({ provocativeness: '3', provocativeness_original: '1' })).toBe('1');
    expect(originalSensitivityValue({ provocativeness: '3' })).toBe('3');
    expect(originalSensitivityValue({ provocativeness: '3', provocativeness_original: '' })).toBe('3');
    expect(originalDifficultyValue({ difficulty: '2', difficulty_original: 3 })).toBe('3');
    expect(originalDifficultyValue({ difficulty: null, difficulty_original: null })).toBe('');
  });
});

describe('applyQuestionEdits', () => {
  it('overlays a staged edit and keeps the options shape', () => {
    const q = { question_id: 'q', options: { option_1: 'a' }, provocativeness: '1' };
    const shown = applyQuestionEdits(q, { options: 'option_1: b', provocativeness: '3' });
    expect(shown.options).toEqual({ option_1: 'b' });
    expect(shown.provocativeness).toBe('3');
    expect(applyQuestionEdits(q, {})).toBe(q);
  });

  it('keeps the model rating as the original on the first change, like the server does', () => {
    const q = { question_id: 'q', provocativeness: '1', difficulty: '2' };
    const first = applyQuestionEdits(q, { provocativeness: '3' });
    expect(first.provocativeness_original).toBe('1');
    expect(first.difficulty_original).toBeUndefined();

    const again = applyQuestionEdits(first, { provocativeness: '2' });
    expect(again.provocativeness_original).toBe('1');

    expect(applyQuestionEdits(q, { provocativeness: '1' }).provocativeness_original).toBeUndefined();
    expect(applyQuestionEdits({ question_id: 'q', provocativeness: '' }, { provocativeness: '2' }).provocativeness_original).toBeUndefined();
    expect(applyQuestionEdits(q, { difficulty: null }).difficulty_original).toBe('2');
  });
});

describe('normalizeQuestionType', () => {
  it('maps the aliases the pipeline tolerates', () => {
    expect(normalizeQuestionType('one')).toBe('one');
    expect(normalizeQuestionType('Single')).toBe('one');
    expect(normalizeQuestionType('multiple_choice')).toBe('multi');
    expect(normalizeQuestionType('open_question')).toBe('open');
    expect(normalizeQuestionType('essay')).toBeNull();
    expect(normalizeQuestionType(undefined)).toBeNull();
  });
});
