import { describe, expect, it } from 'vitest';
import { queueNameSlug } from './queueName';

describe('queueNameSlug', () => {
  it('keeps a realistic Cyrillic name with dashes, dots and digits intact', () => {
    const name = 'Medinskiy Torkunov Istoria Rossii 11 kl Bazovy 2023 — 2 сент. 2026 г.';
    expect(queueNameSlug(name)).toBe(name);
  });

  it('keeps parentheses, commas and dashes', () => {
    expect(queueNameSlug('История (часть 1), том 2 — черновик')).toBe(
      'История (часть 1), том 2 — черновик',
    );
  });

  it('replaces path/URL-hostile characters with underscores', () => {
    expect(queueNameSlug('Тест #1/2\\3?x%y')).toBe('Тест _1_2_3_x_y');
  });

  it('strips control characters and collapses whitespace', () => {
    expect(queueNameSlug('  a\tb\n\nc\x00d   e\x7ff  ')).toBe('a b c_d e_f');
  });

  it('trims to 200 characters', () => {
    const long = 'я'.repeat(300);
    expect(queueNameSlug(long)).toHaveLength(200);
  });
});
