import { describe, expect, it } from 'vitest';
import { buildGiftExport } from './giftExport';
import type { Question } from '../types/documents';

describe('buildGiftExport', () => {
  it('exports single-choice with = and ~', () => {
    const qs: Question[] = [
      {
        chunk_id: 1,
        question_type: 'one',
        task: 'Capital of France?',
        options: { option_1: 'Paris', option_2: 'Lyon', option_3: 'Nice' },
        correct_answer: '1',
      },
    ];
    const gift = buildGiftExport(qs, 'Demo');
    expect(gift).toContain('$CATEGORY: Demo');
    expect(gift).toContain('=Paris');
    expect(gift).toContain('~Lyon');
  });

  it('escapes GIFT special characters', () => {
    const qs: Question[] = [
      {
        chunk_id: 1,
        question_type: 'open',
        task: 'What is {x}?',
        correct_answer: 'a=b',
      },
    ];
    const gift = buildGiftExport(qs);
    expect(gift).toContain('\\{x\\}');
    expect(gift).toContain('=a\\=b');
  });

  it('marks the correct option from a letter answer', () => {
    const qs: Question[] = [
      {
        chunk_id: 1,
        question_type: 'one',
        task: 'Capital?',
        options: { option_1: 'Paris', option_2: 'Lyon' },
        correct_answer: 'A',
      },
    ];
    const gift = buildGiftExport(qs);
    expect(gift).toContain('=Paris');
    expect(gift).toContain('~Lyon');
  });
});

describe('buildGiftExport — multiple answers', () => {
  it('gives distractors a negative weight so ticking everything is not 100%', () => {
    const gift = buildGiftExport([
      {
        question_type: 'multi',
        task: 'Что относится к органам власти?',
        options: { option_1: 'Правительство', option_2: 'Суд', option_3: 'Банк', option_4: 'Клуб' },
        correct_answer: '1,2',
      },
    ]);
    expect(gift).toContain('~%50%Правительство');
    expect(gift).toContain('~%50%Суд');
    expect(gift).toContain('~%-50%Банк');
    expect(gift).toContain('~%-50%Клуб');
    expect(gift).not.toMatch(/~Банк/);
  });

  it('skips a single-choice question whose answer cannot be resolved', () => {
    const gift = buildGiftExport([
      {
        question_type: 'one',
        task: 'Вопрос без ответа?',
        options: { option_1: 'А', option_2: 'Б' },
        correct_answer: '',
      },
    ]);
    // An all-«~» block makes Moodle reject the whole file.
    expect(gift).not.toContain('~А');
    expect(gift).toContain('skipped 1 question');
  });
});
