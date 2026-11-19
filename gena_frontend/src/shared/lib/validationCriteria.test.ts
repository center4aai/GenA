import { describe, expect, it } from 'vitest';
import { CRITERIA, failedSubcriteria, importanceOf, plainJustification, qualityAccordionMode } from './validationCriteria';
import type { Question } from '../types/documents';

const t = (key: string) => key.replace('results:', '');

describe('CRITERIA table', () => {
  it('matches the validator score vectors in length', () => {
    expect(CRITERIA.open.map((b) => [b.block, b.sub.length])).toEqual([
      ['c1_question', 5], ['c2_outputs', 6], ['c4_logic', 4], ['c5_phrase', 2],
    ]);
    expect(CRITERIA.one.map((b) => [b.block, b.sub.length])).toEqual([
      ['c1_question', 5], ['c2_options', 9], ['c3_outputs', 2], ['c4_logic', 4], ['c5_phrase', 2],
    ]);
    expect(CRITERIA.multi.map((b) => b.sub.length)).toEqual([5, 9, 2, 4, 2]);
  });

  it('flags the score-zeroing indices and half weights from validator.py', () => {
    const critical = (kind: 'open' | 'one', block: string) =>
      CRITERIA[kind].find((b) => b.block === block)!.sub.map((s, i) => (s.critical ? i : -1)).filter((i) => i >= 0);
    expect(critical('open', 'c1_question')).toEqual([1, 2, 4]);
    expect(critical('open', 'c2_outputs')).toEqual([1, 2]);
    expect(critical('one', 'c2_options')).toEqual([3, 4, 7]);
    expect(critical('one', 'c3_outputs')).toEqual([0, 1]);
    const half = (kind: 'open' | 'one', block: string) =>
      CRITERIA[kind].find((b) => b.block === block)!.sub.map((s, i) => (s.weight === 0.5 ? i : -1)).filter((i) => i >= 0);
    expect(half('open', 'c1_question')).toEqual([0]);
    expect(half('open', 'c2_outputs')).toEqual([0]);
    expect(half('one', 'c2_options')).toEqual([1, 2]);
  });

  it('derives importance from critical flag and weight', () => {
    const c2 = CRITERIA.one[1].sub;
    expect(importanceOf(c2[3])).toBe('critical');
    expect(importanceOf(c2[0])).toBe('standard');
    expect(importanceOf(c2[2])).toBe('auxiliary');
  });

  it('uses type-specific titles where the prompts differ', () => {
    expect(CRITERIA.one[1].sub[1].titleKey).toBe('results:valSubOptionsRightCountOne');
    expect(CRITERIA.multi[1].sub[1].titleKey).toBe('results:valSubOptionsRightCountMulti');
    expect(CRITERIA.open[2].sub[3].titleKey).toBe('results:valSubLogicLinkOpen');
    expect(CRITERIA.one[3].sub[3].titleKey).toBe('results:valSubLogicLinkChoice');
  });
});

const FAILED: Question = {
  question_type: 'one',
  validation_passed: 'False',
  validation_details: JSON.stringify({
    c1_question: [1, 1, 1, 1, 1],
    c2_options: [1, 1, 1, 1, 0, 0, 1, 1, 1],
    c3_outputs: [1, 1],
    c4_logic: [1, 1, 1, 0],
    c5_phrase: [1, 0],
    __block_weighted__: { c2_options: 6 },
    __block_max__: { c2_options: 8 },
  }),
  validation_justifications: JSON.stringify({
    c2_options: ['', '', '', '', 'варианты 3 и 4 совпадают', 'неоднозначно', '', '', ''],
    c4_logic: ['', '', '', 'вопрос не связан с вариантами'],
  }),
};

describe('failedSubcriteria', () => {
  it('lists only zero scores, grouped by criterion, with justifications and importance', () => {
    const groups = failedSubcriteria(FAILED, t);
    expect(groups.map((g) => g.blockKey)).toEqual(['c2_options', 'c4_logic', 'c5_phrase']);
    expect(groups[0].items).toEqual([
      { id: 'options_duplication', title: 'valSubOptionsDuplication', importance: 'critical', justification: 'варианты 3 и 4 совпадают' },
      { id: 'options_right_unambiguity', title: 'valSubOptionsRightUnambiguity', importance: 'standard', justification: 'неоднозначно' },
    ]);
    expect(groups[1].items[0]).toMatchObject({ id: 'logic_link', justification: 'вопрос не связан с вариантами' });
    expect(groups[2].items[0]).toMatchObject({ id: 'phrase_wording', justification: '' });
  });

  it('falls back to numbered labels for blocks or indices it does not know', () => {
    const odd: Question = {
      question_type: 'open',
      validation_details: { c9_custom: [0], c5_phrase: [1, 1, 0] },
    };
    const groups = failedSubcriteria(odd, t);
    expect(groups.map((g) => g.blockKey)).toEqual(['c5_phrase', 'c9_custom']);
    expect(groups[0].items[0].title).toBe('valBlockC5 #3');
    expect(groups[1].items[0]).toMatchObject({ importance: 'standard', title: 'c9_custom #1' });
  });

  it('returns nothing for unparseable or absent details', () => {
    expect(failedSubcriteria({ validation_details: 'garbage' })).toEqual([]);
    expect(failedSubcriteria({})).toEqual([]);
  });
});

describe('qualityAccordionMode', () => {
  it('is failed for a failed verdict, note for a passed one with zeros, hidden otherwise', () => {
    expect(qualityAccordionMode(FAILED)).toBe('failed');
    expect(qualityAccordionMode({ ...FAILED, validation_passed: 'True' })).toBe('note');
    expect(qualityAccordionMode({ question_type: 'one', validation_passed: 'True', validation_details: '{"c1_question":[1,1,1,1,1]}' })).toBe('hidden');
    expect(qualityAccordionMode({ question_type: 'one' })).toBe('hidden');
    expect(qualityAccordionMode({ question_type: 'one', validation_passed: 'False' })).toBe('failed');
  });
});

describe('plainJustification', () => {
  it('drops markdown emphasis the model likes to add', () => {
    expect(plainJustification('вариант 1 **не упоминается** в тексте, __искажение__')).toBe(
      'вариант 1 не упоминается в тексте, искажение',
    );
    expect(plainJustification(undefined)).toBe('');
    expect(plainJustification('  ok  ')).toBe('ok');
  });
});
