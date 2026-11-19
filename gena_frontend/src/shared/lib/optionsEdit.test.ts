import { describe, expect, it } from 'vitest';
import {
  addOption,
  canAddOption,
  canRemoveOption,
  correctToAnswer,
  isCorrectDisabled,
  optionsToRecord,
  removeOption,
  toggleCorrect,
  tooFewCorrect,
  tooFewOptions,
  tooManyCorrect,
} from './optionsEdit';

const FOUR = ['a', 'b', 'c', 'd'];
const SIX = ['a', 'b', 'c', 'd', 'e', 'f'];

describe('adding and removing options', () => {
  it('allows up to nine options', () => {
    expect(canAddOption(8)).toBe(true);
    expect(canAddOption(9)).toBe(false);
    expect(addOption(FOUR)).toHaveLength(5);
    const nine = Array.from({ length: 9 }, (_, i) => `o${i}`);
    expect(addOption(nine)).toBe(nine);
  });

  it('offers deletion only while more than four remain', () => {
    expect(canRemoveOption(5)).toBe(true);
    expect(canRemoveOption(4)).toBe(false);
    expect(removeOption(FOUR, [2], 1)).toEqual({ texts: FOUR, correct: [2] });
  });

  it('renumbers and remaps correct indices on removal', () => {
    expect(removeOption(SIX, [1, 3, 5], 3)).toEqual({ texts: ['a', 'b', 'd', 'e', 'f'], correct: [1, 4] });
    expect(removeOption(SIX, [6], 2)).toEqual({ texts: ['a', 'c', 'd', 'e', 'f'], correct: [5] });
    expect(removeOption(SIX, [1], 9)).toEqual({ texts: SIX, correct: [1] });
  });
});

describe('correct marks', () => {
  it('single choice is exclusive and cannot be unmarked', () => {
    expect(toggleCorrect([4], 2, 'one', 4)).toEqual([2]);
    expect(toggleCorrect([2], 2, 'one', 4)).toEqual([2]);
    expect(toggleCorrect([1, 3], 2, 'one', 4)).toEqual([2]);
  });

  it('collapses leftover multi marks to the clicked option once the type is single', () => {
    expect(toggleCorrect([1, 3], 1, 'one', 4)).toEqual([1]);
    expect(toggleCorrect([1, 3], 3, 'one', 4)).toEqual([3]);
  });

  it('flags the states a type switch leaves behind', () => {
    expect(tooManyCorrect([1, 3], 'one')).toBe(true);
    expect(tooManyCorrect([1], 'one')).toBe(false);
    expect(tooManyCorrect([1, 3], 'multi')).toBe(false);
    expect(tooFewOptions(0)).toBe(true);
    expect(tooFewOptions(3)).toBe(true);
    expect(tooFewOptions(4)).toBe(false);
  });

  it('multi caps at count minus one and disables the remaining box', () => {
    expect(toggleCorrect([1, 2], 3, 'multi', 4)).toEqual([1, 2, 3]);
    expect(toggleCorrect([1, 2, 3], 4, 'multi', 4)).toEqual([1, 2, 3]);
    expect(isCorrectDisabled([1, 2, 3], 4, 'multi', 4)).toBe(true);
    expect(isCorrectDisabled([1, 2, 3], 3, 'multi', 4)).toBe(false);
    expect(isCorrectDisabled([1, 2, 3], 4, 'one', 4)).toBe(false);
    expect(toggleCorrect([1, 2, 3], 2, 'multi', 4)).toEqual([1, 3]);
  });

  it('knows when too few options are marked', () => {
    expect(tooFewCorrect([1], 'multi')).toBe(true);
    expect(tooFewCorrect([1, 4], 'multi')).toBe(false);
    expect(tooFewCorrect([], 'one')).toBe(true);
    expect(tooFewCorrect([2], 'one')).toBe(false);
  });
});

describe('serialization', () => {
  it('writes option_N keys and comma-joined sorted indices', () => {
    expect(optionsToRecord(['x', 'y'])).toEqual({ option_1: 'x', option_2: 'y' });
    expect(correctToAnswer([5, 1, 3])).toBe('1,3,5');
    expect(correctToAnswer([2])).toBe('2');
    expect(correctToAnswer([])).toBe('');
  });
});
