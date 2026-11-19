import { isValidationPassed } from '@/shared/lib/progress';
import type { Question } from '@/shared/types/documents';

/** Scores produced by the validator; they stop meaning anything once the
 *  question they describe is rewritten. */
export const VALIDATION_FIELDS = [
  'validation_passed',
  'validation_score',
  'validation_threshold',
  'validation_details',
  'validation_justifications',
  'retry_count',
] as const;

/** Fields whose change invalidates the stored validation verdict. Sensitivity
 *  and difficulty are expert judgements about the question, not the question
 *  itself, so editing them keeps the verdict. */
export const REVALIDATING_FIELDS = ['task', 'options', 'correct_answer'] as const;

/** Flag the question for re-validation and drop the now-stale scores. */
export function markNeedsRevalidation(q: Question): Question {
  const copy: Question = { ...q, needs_revalidation: true, validation_is_current: false };
  for (const key of VALIDATION_FIELDS) {
    delete (copy as Record<string, unknown>)[key];
  }
  return copy;
}

/**
 * Whether the stored verdict describes the question as it is now. Mirrors
 * dataset_api `_validation_is_current`: rows from before the flag existed
 * carry only needs_revalidation (and nulled scores).
 */
export function isValidationCurrent(q: Question): boolean {
  if (typeof q.validation_is_current === 'boolean') return q.validation_is_current;
  if (q.needs_revalidation) return false;
  return isValidationPassed(q) !== null;
}

export type QualityState = 'passed' | 'failed' | 'revalidation_required';

/**
 * The one Quality validation status a question card shows. A stale verdict is
 * never shown as passed/failed: it describes wording that no longer exists.
 */
export function qualityState(q: Question): QualityState {
  if (!isValidationCurrent(q)) return 'revalidation_required';
  return isValidationPassed(q) ? 'passed' : 'failed';
}

/** True when a patch touches wording that the validator scored. */
export function editNeedsRevalidation(patch: Partial<Question>): boolean {
  return REVALIDATING_FIELDS.some((key) => key in patch);
}

/** Null out the stale scores in an outgoing payload (the API stores nulls
 *  rather than dropping keys). */
export function clearedValidationPayload(): Record<string, null> {
  return Object.fromEntries(VALIDATION_FIELDS.map((key) => [key, null]));
}
