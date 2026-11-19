import { describe, expect, it } from 'vitest';
import { blockLabel, parseValidationDetails, parseValidationJustifications } from './validation';

describe('validation helpers', () => {
  it('blockLabel returns known labels', () => {
    expect(blockLabel('c1_question')).toBe('Question phrasing');
    expect(blockLabel('unknown_block')).toBe('unknown_block');
  });

  it('parseValidationDetails accepts object input', () => {
    expect(parseValidationDetails({ c1_question: [1, 0] })).toEqual({ c1_question: [1, 0] });
  });

  it('parseValidationDetails parses python-like dict strings', () => {
    const raw = "{'c1_question': [1, 0], 'c2_outputs': [1]}";
    expect(parseValidationDetails(raw)).toEqual({ c1_question: [1, 0], c2_outputs: [1] });
  });

  it('parseValidationJustifications maps arrays to strings', () => {
    const raw = { c1_question: ['bad wording', ''] };
    expect(parseValidationJustifications(raw)).toEqual({ c1_question: ['bad wording', ''] });
  });

  it('parseValidationJustifications parses python-like dict strings', () => {
    const raw = "{'c1_question': ['bad wording']}";
    expect(parseValidationJustifications(raw)).toEqual({ c1_question: ['bad wording'] });
  });

  it('blockLabel uses the translator when provided', () => {
    const t = (key: string) => (key === 'results:valBlockC1' ? 'Формулировка вопроса' : key);
    expect(blockLabel('c1_question', t)).toBe('Формулировка вопроса');
  });
});
