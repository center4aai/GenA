import { describe, expect, it } from 'vitest';
import {
  clearedValidationPayload,
  editNeedsRevalidation,
  markNeedsRevalidation,
  isValidationCurrent,
  qualityState,
} from './revalidation';
import type { Question } from '../types/documents';

const SCORED: Question = {
  question_id: 'q1',
  task: 'Original?',
  validation_passed: 'True',
  validation_score: '17/20',
  validation_threshold: '14',
  retry_count: '1',
};

describe('editNeedsRevalidation', () => {
  it('is true when the wording the validator scored changes', () => {
    expect(editNeedsRevalidation({ task: 'Edited?' })).toBe(true);
    expect(editNeedsRevalidation({ options: 'option_1: x' })).toBe(true);
    expect(editNeedsRevalidation({ correct_answer: '2' })).toBe(true);
  });

  it('is false for expert ratings and export flags', () => {
    expect(editNeedsRevalidation({ provocativeness: '3' })).toBe(false);
    expect(editNeedsRevalidation({ difficulty: '1' })).toBe(false);
    expect(editNeedsRevalidation({ excluded_from_export: true })).toBe(false);
    expect(editNeedsRevalidation({})).toBe(false);
  });
});

describe('markNeedsRevalidation', () => {
  it('flags the question and drops the stale scores', () => {
    const marked = markNeedsRevalidation(SCORED);
    expect(marked.needs_revalidation).toBe(true);
    expect(marked).not.toHaveProperty('validation_passed');
    expect(marked).not.toHaveProperty('validation_score');
    expect(marked).not.toHaveProperty('retry_count');
    expect(marked.task).toBe('Original?');
  });

  it('leaves the original untouched', () => {
    markNeedsRevalidation(SCORED);
    expect(SCORED.validation_score).toBe('17/20');
  });
});

describe('clearedValidationPayload', () => {
  it('nulls every scored field for the API', () => {
    const payload = clearedValidationPayload();
    expect(payload.validation_passed).toBeNull();
    expect(payload.validation_justifications).toBeNull();
    expect(Object.values(payload).every((v) => v === null)).toBe(true);
  });
});

describe('isValidationCurrent / qualityState', () => {
  it('follows the explicit flag when the server sends it', () => {
    expect(qualityState({ validation_passed: 'True', validation_is_current: true })).toBe('passed');
    expect(qualityState({ validation_passed: 'False', validation_is_current: true })).toBe('failed');
    expect(qualityState({ validation_passed: 'True', validation_is_current: false })).toBe('revalidation_required');
    expect(qualityState({ validation_passed: 'False', validation_is_current: false })).toBe('revalidation_required');
  });

  it('derives currency for rows written before the flag existed', () => {
    expect(isValidationCurrent({ validation_passed: 'True' })).toBe(true);
    expect(isValidationCurrent({ validation_passed: 'True', needs_revalidation: true })).toBe(false);
    expect(isValidationCurrent({})).toBe(false);
    expect(qualityState({})).toBe('revalidation_required');
  });

  it('marks a rephrased question stale', () => {
    expect(markNeedsRevalidation({ validation_passed: 'True' }).validation_is_current).toBe(false);
  });
});
