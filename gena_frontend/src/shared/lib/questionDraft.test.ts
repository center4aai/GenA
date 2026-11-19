import { describe, expect, it } from 'vitest';
import { buildQuestionPatches } from './questionEdits';
import { draftFromQuestion, draftIsDirty, draftToEdit, draftValidation, withQuestionType } from './questionDraft';
import type { Question } from '../types/documents';

const ONE: Question = {
  question_id: 'q1',
  question_type: 'one',
  task: 'Which?',
  options: { option_1: 'a', option_2: 'b', option_3: 'c', option_4: 'd' },
  correct_answer: '4',
  provocativeness: '1',
  difficulty: '2',
};
const MULTI: Question = { ...ONE, question_id: 'q2', question_type: 'multi', correct_answer: '1,3' };
const OPEN: Question = { question_id: 'q3', question_type: 'open', task: 'Why?', options: '', correct_answer: 'Because.' };

describe('draftFromQuestion', () => {
  it('reads options as an ordered list with 1-based correct marks', () => {
    const d = draftFromQuestion(MULTI);
    expect(d.optionTexts).toEqual(['a', 'b', 'c', 'd']);
    expect(d.correct).toEqual([1, 3]);
    expect(d.provocativeness).toBe('1');
    expect(d.difficulty).toBe('2');
    expect(d.optionsLocked).toBe(false);
  });

  it('parses legacy option_N text and locks unparseable legacy strings', () => {
    const text = draftFromQuestion({ ...ONE, options: 'option_1: x\noption_2: y', correct_answer: 'option_2' });
    expect(text.optionTexts).toEqual(['x', 'y']);
    expect(text.correct).toEqual([2]);
    const locked = draftFromQuestion({ ...ONE, options: 'A) x B) y' });
    expect(locked.optionsLocked).toBe(true);
    expect(draftFromQuestion(OPEN).optionsLocked).toBe(false);
  });

  it('is not dirty right after loading', () => {
    expect(draftIsDirty(draftFromQuestion(ONE), ONE)).toBe(false);
    expect(draftIsDirty(draftFromQuestion(OPEN), OPEN)).toBe(false);
    expect(draftIsDirty({ ...draftFromQuestion(ONE), touchedCorrect: true }, ONE)).toBe(false);
  });
});

describe('draftToEdit', () => {
  it('emits only changed fields', () => {
    const d = { ...draftFromQuestion(ONE), difficulty: '' as const, task: 'Which one?' };
    expect(draftToEdit(d, ONE)).toEqual({ task: 'Which one?', difficulty: null });
  });

  it('rebuilds options and correct_answer when the list changes', () => {
    const d = draftFromQuestion(ONE);
    d.optionTexts = [...d.optionTexts, 'e'];
    d.correct = [5];
    expect(draftToEdit(d, ONE)).toEqual({
      options: { option_1: 'a', option_2: 'b', option_3: 'c', option_4: 'd', option_5: 'e' },
      correct_answer: '5',
    });
  });

  it('writes several correct marks in the worker format', () => {
    const d = draftFromQuestion(MULTI);
    d.correct = [2, 4];
    expect(draftToEdit(d, MULTI)).toEqual({ correct_answer: '2,4' });
  });

  it('keeps the legacy string shape for legacy questions', () => {
    const legacy: Question = { ...ONE, options: 'option_1: x\noption_2: y\noption_3: z\noption_4: w', correct_answer: '1' };
    const d = draftFromQuestion(legacy);
    d.optionTexts[1] = 'Y';
    expect(draftToEdit(d, legacy)).toEqual({ options: 'option_1: x\noption_2: Y\noption_3: z\noption_4: w' });
  });

  it('never writes locked options', () => {
    const locked: Question = { ...ONE, options: 'A) x B) y' };
    const d = { ...draftFromQuestion(locked), optionTexts: ['p'], correct: [1] };
    expect(draftToEdit(d, locked)).toEqual({});
    expect(draftIsDirty(d, locked)).toBe(false);
  });

  it('edits the free-text answer of open questions', () => {
    const d = { ...draftFromQuestion(OPEN), correct_answer: 'Because it is.' };
    expect(draftToEdit(d, OPEN)).toEqual({ correct_answer: 'Because it is.' });
  });

  it('turns an open question into a choice one with a proper options record', () => {
    const d = { ...draftFromQuestion(OPEN), question_type: 'one' as const, optionTexts: ['a', 'b', 'c', 'd'], correct: [2] };
    expect(draftToEdit(d, OPEN)).toEqual({
      question_type: 'one',
      options: { option_1: 'a', option_2: 'b', option_3: 'c', option_4: 'd' },
      correct_answer: '2',
    });
  });

  it('turns a multi into a single once one mark is left', () => {
    const d = { ...draftFromQuestion(MULTI), question_type: 'one' as const, correct: [3] };
    expect(draftToEdit(d, MULTI)).toEqual({ question_type: 'one', correct_answer: '3' });
  });

  it('turns a choice question open: answer text instead of an index, options cleared', () => {
    const patch = withQuestionType(draftFromQuestion(MULTI), 'open');
    expect(patch).toEqual({ question_type: 'open', correct_answer: 'a, c' });
    const d = { ...draftFromQuestion(MULTI), ...patch };
    expect(draftIsDirty(d, MULTI)).toBe(true);
    expect(draftToEdit(d, MULTI)).toEqual({ question_type: 'open', correct_answer: 'a, c', options: '' });
  });

  it('keeps the free-text answer when an open question is switched away and back', () => {
    const toChoice = withQuestionType(draftFromQuestion(OPEN), 'one');
    expect(toChoice).toEqual({ question_type: 'one' });
    const back = withQuestionType({ ...draftFromQuestion(OPEN), ...toChoice }, 'open');
    expect(back).toEqual({ question_type: 'open', correct_answer: 'Because.' });
  });

  it('composes with buildQuestionPatches so wording changes invalidate the verdict', () => {
    const scored: Question = { ...ONE, validation_passed: 'True', validation_score: '20/20.5' };
    const wording = draftToEdit({ ...draftFromQuestion(scored), task: 'New?' }, scored);
    const [p1] = buildQuestionPatches([scored], { q1: wording });
    expect(p1.needs_revalidation).toBe(true);
    expect(p1).not.toHaveProperty('validation_passed');
    const rating = draftToEdit({ ...draftFromQuestion(scored), provocativeness: '3' }, scored);
    const [p2] = buildQuestionPatches([scored], { q1: rating });
    expect(p2).not.toHaveProperty('needs_revalidation');
  });
});

describe('draftValidation', () => {
  it('blocks saving with a blank option or too few correct marks', () => {
    const blank = { ...draftFromQuestion(ONE), optionTexts: ['a', 'b', 'c', 'd', ''] };
    expect(draftValidation(blank)).toMatchObject({ emptyOption: true, canSave: false });
    const few = { ...draftFromQuestion(MULTI), correct: [1] };
    expect(draftValidation(few)).toMatchObject({ tooFewCorrect: true, canSave: false });
    expect(draftValidation(draftFromQuestion(MULTI)).canSave).toBe(true);
    expect(draftValidation(draftFromQuestion(OPEN)).canSave).toBe(true);
  });

  it('asks for four options after an open question becomes a choice one', () => {
    const switched = { ...draftFromQuestion(OPEN), question_type: 'one' as const };
    expect(draftValidation(switched)).toMatchObject({ tooFewOptions: true, canSave: false });
    const three = { ...switched, optionTexts: ['a', 'b', 'c'], correct: [1] };
    expect(draftValidation(three)).toMatchObject({ tooFewOptions: true, canSave: false });
    const four = { ...switched, optionTexts: ['a', 'b', 'c', 'd'], correct: [1] };
    expect(draftValidation(four)).toMatchObject({ tooFewOptions: false, canSave: true });
    expect(draftValidation(draftFromQuestion(OPEN)).tooFewOptions).toBe(false);
  });

  it('asks for exactly one mark after a multi becomes single', () => {
    const switched = { ...draftFromQuestion(MULTI), question_type: 'one' as const };
    expect(draftValidation(switched)).toMatchObject({ tooManyCorrect: true, canSave: false });
    expect(draftValidation({ ...switched, correct: [3] })).toMatchObject({ tooManyCorrect: false, canSave: true });
    expect(draftValidation(draftFromQuestion(MULTI)).tooManyCorrect).toBe(false);
  });
});
