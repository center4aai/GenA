import { describe, expect, it } from 'vitest';
import {
  buildQuestionPatches,
  exportTogglePatch,
  isExcludedFromExport,
  withTransformEditStatus,
} from './questionEdits';
import type { Question } from '../types/documents';

const QUESTIONS: Question[] = [
  {
    question_id: 'q1',
    task: 'Original?',
    options: { option_1: 'a', option_2: 'b' },
    correct_answer: '1',
    provocativeness: '2',
    difficulty: '3',
    validation_passed: 'True',
    validation_score: '17/20',
  },
  { question_id: 'q2', task: 'Second?', options: '', correct_answer: 'x' },
];

describe('buildQuestionPatches', () => {
  it('sends only the questions that changed', () => {
    const patches = buildQuestionPatches(QUESTIONS, { q2: { task: 'Edited?' } });
    expect(patches).toHaveLength(1);
    expect(patches[0].question_id).toBe('q2');
  });

  it('sends only the fields that changed', () => {
    const patches = buildQuestionPatches(QUESTIONS, { q1: { provocativeness: '1' } });
    expect(patches[0]).toMatchObject({ question_id: 'q1', provocativeness: '1' });
    expect(patches[0]).not.toHaveProperty('task');
    expect(patches[0]).not.toHaveProperty('options');
  });

  it('is empty when nothing was edited', () => {
    expect(buildQuestionPatches(QUESTIONS, {})).toEqual([]);
  });

  it('skips edits that cannot be addressed without a question_id', () => {
    const orphan: Question[] = [{ task: 'No id?' }];
    expect(buildQuestionPatches(orphan, { q1: { task: 'Edited?' } })).toEqual([]);
  });

  it('flags re-validation but keeps the stored verdict when wording changes', () => {
    // ТЗ 10.4: passed survives at the data level; the server marks it stale.
    const [patch] = buildQuestionPatches(QUESTIONS, { q1: { task: 'Edited?' } });
    expect(patch.needs_revalidation).toBe(true);
    expect(patch).not.toHaveProperty('validation_passed');
    expect(patch).not.toHaveProperty('validation_score');
  });

  it('keeps the validation verdict when only the expert ratings change', () => {
    const [patch] = buildQuestionPatches(QUESTIONS, { q1: { difficulty: '1' } });
    expect(patch).not.toHaveProperty('needs_revalidation');
    expect(patch).not.toHaveProperty('validation_passed');
  });

  it('normalizes an emptied difficulty to null', () => {
    const [patch] = buildQuestionPatches(QUESTIONS, { q1: { difficulty: '—' } });
    expect(patch.difficulty).toBeNull();
  });

  it('keeps edited options in the shape the question already used', () => {
    const [patch] = buildQuestionPatches(QUESTIONS, {
      q1: { options: 'option_1: x\noption_2: y' },
    });
    expect(patch.options).toEqual({ option_1: 'x', option_2: 'y' });
  });

  it('carries the export exclusion in both directions', () => {
    expect(
      buildQuestionPatches(QUESTIONS, { q1: { excluded_from_export: true } })[0]
        .excluded_from_export,
    ).toBe(true);
    expect(
      buildQuestionPatches(QUESTIONS, { q1: { excluded_from_export: false } })[0]
        .excluded_from_export,
    ).toBe(false);
  });
});

describe('isExcludedFromExport', () => {
  it('is false unless the question was explicitly excluded', () => {
    expect(isExcludedFromExport({ task: 'q' })).toBe(false);
    expect(isExcludedFromExport({ task: 'q', excluded_from_export: false })).toBe(false);
    expect(isExcludedFromExport({ task: 'q', excluded_from_export: true })).toBe(true);
  });
});

describe('buildQuestionPatches and edit status', () => {
  it('never sends edit_status — the server derives it from the changed fields', () => {
    const [patch] = buildQuestionPatches(QUESTIONS, {
      q1: { task: 'Edited?', edit_status: 'paraphrased' } as Partial<Question>,
    });
    expect(patch).not.toHaveProperty('edit_status');
  });
});

describe('withTransformEditStatus', () => {
  const now = new Date('2026-09-08T10:00:00.000Z');
  const original: Question = {
    question_id: 'q1',
    task: 'Original?',
    options: { option_1: 'a', option_2: 'b' },
    correct_answer: '1',
  };

  it('marks a reworded task as paraphrased', () => {
    const out = withTransformEditStatus(original, { ...original, task: 'Reworded?' }, now);
    expect(out.edit_status).toBe('paraphrased');
    expect(out.edited_at).toBe('2026-09-08T10:00:00.000Z');
  });

  it('treats a shuffle (options moved, task unchanged) as no edit', () => {
    const shuffled: Question = {
      ...original,
      options: { option_1: 'b', option_2: 'a' },
      correct_answer: '2',
    };
    const out = withTransformEditStatus(original, shuffled, now);
    expect(out.edit_status).toBeNull();
    expect(out.options).toEqual(shuffled.options);
  });

  it('carries an earlier manual edit through a shuffle-only transform', () => {
    const edited: Question = { ...original, edit_status: 'user_edited', edited_at: '2026-09-01T00:00:00Z' };
    const out = withTransformEditStatus(edited, { ...edited, correct_answer: '2' }, now);
    expect(out.edit_status).toBe('user_edited');
    expect(out.edited_at).toBe('2026-09-01T00:00:00Z');
  });

  it('does not call a rephrase that returned the same text a paraphrase', () => {
    const out = withTransformEditStatus(original, { ...original, task: ' Original? ' }, now);
    expect(out.edit_status).toBeNull();
  });
});

describe('exportTogglePatch', () => {
  it('sends exactly the one field the switch controls', () => {
    expect(exportTogglePatch({ question_id: 'q1' }, true)).toEqual({
      question_id: 'q1',
      excluded_from_export: true,
    });
  });

  it('refuses questions the backend cannot address', () => {
    expect(exportTogglePatch({ question_id: 'ds:00003' }, true)).toBeNull();
    expect(exportTogglePatch({}, false)).toBeNull();
  });
});
