/**
 * Rules for editing answer options in the question card. Indices are 1-based
 * everywhere, matching `option_N` keys and the `correct_answer` tokens.
 */

/** Ceiling comes from the generator's JSON schema (option_1 … option_9). */
export const MAX_OPTIONS = 9;
/** Floor for a usable choice question; generated sets always have at least 4. */
export const MIN_OPTIONS = 4;

export type ChoiceMode = 'one' | 'multi';

export function canAddOption(count: number): boolean {
  return count < MAX_OPTIONS;
}

/** Deletion is offered only while the set stays above the floor afterwards. */
export function canRemoveOption(count: number): boolean {
  return count > MIN_OPTIONS;
}

export function addOption(texts: string[]): string[] {
  return canAddOption(texts.length) ? [...texts, ''] : texts;
}

/** Drop option `index`, renumber the rest and remap the correct indices. */
export function removeOption(
  texts: string[],
  correct: number[],
  index: number,
): { texts: string[]; correct: number[] } {
  if (!canRemoveOption(texts.length) || index < 1 || index > texts.length) return { texts, correct };
  return {
    texts: texts.filter((_, i) => i !== index - 1),
    correct: correct.filter((c) => c !== index).map((c) => (c > index ? c - 1 : c)),
  };
}

function sorted(indices: number[]): number[] {
  return [...new Set(indices)].sort((a, b) => a - b);
}

/**
 * Flip the "correct" mark of option `index`.
 * one:   exactly one mark, radio behaviour (a click on the sole marked option is
 *        a no-op). With several marks left over from a multi, any click keeps
 *        just the clicked option — that is how "remove the extra marks" works.
 * multi: at most count-1 marks — a click beyond the cap is ignored.
 */
export function toggleCorrect(correct: number[], index: number, mode: ChoiceMode, count: number): number[] {
  const marked = correct.includes(index);
  if (mode === 'one') return marked && correct.length === 1 ? correct : [index];
  if (marked) return correct.filter((c) => c !== index);
  if (correct.length >= multiCap(count)) return correct;
  return sorted([...correct, index]);
}

/** Every option marked correct is no question at all: multi keeps one distractor. */
export function multiCap(count: number): number {
  return Math.max(1, count - 1);
}

export function isCorrectDisabled(correct: number[], index: number, mode: ChoiceMode, count: number): boolean {
  if (mode !== 'multi') return false;
  return !correct.includes(index) && correct.length >= multiCap(count);
}

/** Multi needs two correct options to be a multi; one needs its single answer. */
export function tooFewCorrect(correct: number[], mode: ChoiceMode): boolean {
  return mode === 'multi' ? correct.length < 2 : correct.length < 1;
}

/** Several marks on a single-choice question: what a multi looks like right after switching to `one`. */
export function tooManyCorrect(correct: number[], mode: ChoiceMode): boolean {
  return mode === 'one' && correct.length > 1;
}

/** Below the floor: an open question just switched to a choice kind, until enough options are added. */
export function tooFewOptions(count: number): boolean {
  return count < MIN_OPTIONS;
}

export function optionsToRecord(texts: string[]): Record<string, string> {
  return Object.fromEntries(texts.map((text, i) => [`option_${i + 1}`, text]));
}

/** "3" for a single answer, "1,3,5" for several — the worker's own format. */
export function correctToAnswer(correct: number[]): string {
  return sorted(correct).join(',');
}
