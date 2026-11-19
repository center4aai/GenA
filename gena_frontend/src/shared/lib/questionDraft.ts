import {
  correctToAnswer,
  optionsToRecord,
  tooFewCorrect,
  tooFewOptions,
  tooManyCorrect,
  type ChoiceMode,
} from '@/shared/lib/optionsEdit';
import {
  isCorrectOption,
  normOptions,
  optionsEntries,
  parseOptionsText,
  resolveAnswerText,
} from '@/shared/lib/optionsFormat';
import {
  difficultyValue,
  normalizeQuestionType,
  sensitivityValue,
  type QuestionKind,
} from '@/shared/lib/questionFields';
import type { Question } from '@/shared/types/documents';

export type LevelValue = '' | '1' | '2' | '3';

/**
 * The question card's form model. Options live as an ordered list of texts
 * plus 1-based indices of the correct ones; the server's `option_N` record and
 * `correct_answer` tokens are rebuilt on save.
 */
export interface QuestionDraft {
  question_type: QuestionKind;
  provocativeness: LevelValue;
  difficulty: LevelValue;
  task: string;
  /** Free-text answer of an open question. */
  correct_answer: string;
  optionTexts: string[];
  correct: number[];
  /** Set once the user touched a correct-mark; from then on `one` enforces exclusivity. */
  touchedCorrect: boolean;
  /**
   * True when a choice question's options could not be read as `option_N`
   * entries (legacy free text). They are shown read-only and never written.
   */
  optionsLocked: boolean;
}

function asLevel(value: string): LevelValue {
  return value === '1' || value === '2' || value === '3' ? value : '';
}

function optionsRecord(options: Question['options']): Record<string, string> | null {
  if (options == null) return null;
  if (typeof options === 'string') return options.trim() ? parseOptionsText(options) : null;
  return options;
}

export function draftFromQuestion(q: Question): QuestionDraft {
  const kind = normalizeQuestionType(q.question_type) ?? 'open';
  const entries = optionsEntries(optionsRecord(q.options));
  const optionTexts = entries.map((e) => e.text);
  const correct = entries
    .map((e, i) => (isCorrectOption(q.correct_answer, e.key, e.index) ? i + 1 : 0))
    .filter((i) => i > 0);
  const rawOptions = typeof q.options === 'string' ? q.options.trim() : '';
  return {
    question_type: kind,
    provocativeness: asLevel(sensitivityValue(q)),
    difficulty: asLevel(difficultyValue(q)),
    task: String(q.task ?? ''),
    correct_answer: String(q.correct_answer ?? ''),
    optionTexts,
    correct,
    touchedCorrect: false,
    optionsLocked: kind !== 'open' && entries.length === 0 && rawOptions !== '',
  };
}

export function isChoiceKind(kind: QuestionKind): kind is ChoiceMode {
  return kind === 'one' || kind === 'multi';
}

/**
 * The draft patch for picking a new type in the card. Choice → open carries
 * the marked options' text over as the free-text answer, so the field does
 * not show a bare index like "4"; nothing else is touched — the validation
 * hints tell the user what the new type still needs.
 */
export function withQuestionType(draft: QuestionDraft, kind: QuestionKind): Partial<QuestionDraft> {
  if (kind === 'open' && isChoiceKind(draft.question_type) && !draft.optionsLocked) {
    const text = resolveAnswerText(optionsToRecord(draft.optionTexts), correctToAnswer(draft.correct));
    return { question_type: kind, correct_answer: text || draft.correct_answer };
  }
  return { question_type: kind };
}

function sameList(a: readonly unknown[], b: readonly unknown[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function sameCorrect(a: number[], b: number[]): boolean {
  return correctToAnswer(a) === correctToAnswer(b);
}

/** True when saving would send anything. `touchedCorrect` alone is not a change. */
export function draftIsDirty(draft: QuestionDraft, q: Question): boolean {
  const base = draftFromQuestion(q);
  if (draft.question_type !== base.question_type) return true;
  if (draft.provocativeness !== base.provocativeness) return true;
  if (draft.difficulty !== base.difficulty) return true;
  if (draft.task !== base.task) return true;
  if (isChoiceKind(draft.question_type)) {
    if (draft.optionsLocked) return false;
    return !sameList(draft.optionTexts, base.optionTexts) || !sameCorrect(draft.correct, base.correct);
  }
  return draft.correct_answer !== base.correct_answer;
}

export interface DraftValidation {
  /** An option field is blank (a freshly added one, or an emptied existing one). */
  emptyOption: boolean;
  /** one: no correct mark; multi: fewer than two. */
  tooFewCorrect: boolean;
  /** A choice kind with fewer than MIN_OPTIONS options — an open question switched to one/multi. */
  tooFewOptions: boolean;
  /** `one` with more than one mark — a multi switched to one. */
  tooManyCorrect: boolean;
  canSave: boolean;
}

/**
 * The rules are about the draft's state, not about which control was touched,
 * so switching the type, adding options and (un)marking answers all converge
 * on the same hints.
 */
export function draftValidation(draft: QuestionDraft): DraftValidation {
  const choice = isChoiceKind(draft.question_type) && !draft.optionsLocked;
  const mode = draft.question_type as ChoiceMode;
  const emptyOption = choice && draft.optionTexts.some((t) => t.trim() === '');
  const fewOptions = choice && tooFewOptions(draft.optionTexts.length);
  const fewCorrect = choice && tooFewCorrect(draft.correct, mode);
  const manyCorrect = choice && tooManyCorrect(draft.correct, mode);
  return {
    emptyOption,
    tooFewCorrect: fewCorrect,
    tooFewOptions: fewOptions,
    tooManyCorrect: manyCorrect,
    canSave: !emptyOption && !fewOptions && !fewCorrect && !manyCorrect,
  };
}

/**
 * Only the fields that differ from the server question, in the shape
 * `buildQuestionPatches` expects. Options keep the storage shape the question
 * already had: a record normally, `option_N: text` lines for a legacy string.
 * A question that had no options at all (open → choice) gets the record.
 */
export function draftToEdit(draft: QuestionDraft, q: Question): Partial<Question> {
  const base = draftFromQuestion(q);
  const edit: Partial<Question> = {};
  if (draft.question_type !== base.question_type) edit.question_type = draft.question_type;
  if (draft.provocativeness !== base.provocativeness) edit.provocativeness = draft.provocativeness;
  if (draft.difficulty !== base.difficulty) edit.difficulty = draft.difficulty === '' ? null : draft.difficulty;
  if (draft.task !== base.task) edit.task = draft.task;

  if (isChoiceKind(draft.question_type)) {
    if (!draft.optionsLocked) {
      const optionsChanged = !sameList(draft.optionTexts, base.optionTexts);
      if (optionsChanged) {
        const record = optionsToRecord(draft.optionTexts);
        const legacyString = typeof q.options === 'string' && q.options.trim() !== '';
        edit.options = legacyString ? normOptions(record) : record;
      }
      const answer = correctToAnswer(draft.correct);
      if (optionsChanged || !sameCorrect(draft.correct, base.correct) || !isChoiceKind(base.question_type)) {
        if (answer !== String(q.correct_answer ?? '')) edit.correct_answer = answer;
      }
    }
  } else {
    if (draft.correct_answer !== base.correct_answer) edit.correct_answer = draft.correct_answer;
    // A choice question turned open leaves no option_N behind (exports would print them).
    if (isChoiceKind(base.question_type)) edit.options = '';
  }
  return edit;
}
