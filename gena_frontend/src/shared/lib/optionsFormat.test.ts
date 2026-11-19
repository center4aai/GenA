import { describe, expect, it } from 'vitest';
import { coerceOptionsLike, normOptions, parseOptionsText } from './optionsFormat';

describe('optionsFormat', () => {
  it('normOptions sorts keys and formats lines', () => {
    const text = normOptions({ option_2: 'B', option_1: 'A', option_10: 'C' });
    expect(text).toContain('option_1: A');
    expect(text.indexOf('option_1')).toBeLessThan(text.indexOf('option_10'));
  });

  it('parseOptionsText round-trips with normOptions', () => {
    const dict = { option_1: 'Yes', option_2: 'No' };
    const parsed = parseOptionsText(normOptions(dict));
    expect(parsed).toEqual(dict);
  });

  it('coerceOptionsLike preserves dict edits', () => {
    const original = { option_1: 'a' };
    const edited = { option_1: 'b' };
    expect(coerceOptionsLike(original, edited)).toEqual(edited);
  });

  it('coerceOptionsLike parses text when original was dict', () => {
    const original = { option_1: 'a' };
    expect(coerceOptionsLike(original, 'option_1: updated')).toEqual({ option_1: 'updated' });
  });

  it('coerceOptionsLike keeps a legacy string a string but lets a question without options take a record', () => {
    expect(coerceOptionsLike('option_1: a', { option_1: 'b' })).toBe('option_1: b');
    expect(coerceOptionsLike('', { option_1: 'b' })).toEqual({ option_1: 'b' });
    expect(coerceOptionsLike(undefined, { option_1: 'b' })).toEqual({ option_1: 'b' });
    expect(coerceOptionsLike('', 'option_1: b')).toBe('option_1: b');
    expect(coerceOptionsLike(undefined, undefined)).toBe('');
  });

});
