import { coerceOptionsLike } from '@/shared/lib/optionsFormat';
import type { Question } from '@/shared/types/documents';

export type QuestionKind = 'one' | 'multi' | 'open';

/** Ids `ensureQids` fabricates look like `<datasetId>:00042`; uuids never contain a colon. */
const SYNTHETIC_ID = /:\d{5}$/;

/**
 * Give every question an id the UI can key drafts and React lists on.
 * Synthetic ids are for the UI only — the backend cannot address them, so
 * anything that PATCHes must check `hasRealQuestionId` first.
 */
export function ensureQids(datasetId: string, questions: Question[]): Question[] {
  return questions.map((q, idx) => ({
    ...q,
    question_id: q.question_id ?? `${datasetId}:${String(idx).padStart(5, '0')}`,
  }));
}

/** True when the id came from the server (a PATCH for it will be accepted). */
export function hasRealQuestionId(q: Question): boolean {
  const id = String(q.question_id ?? '').trim();
  return id !== '' && !SYNTHETIC_ID.test(id);
}

/** Sensitivity level as stored ("1" | "2" | "3"), "" when the assessment failed. */
export function sensitivityValue(q: Question): string {
  const raw = q.provocativeness ?? q.sensitivity_level;
  return raw == null || String(raw).trim() === '' ? '' : String(raw);
}

/** A stored level as a string, "" for absent values and the "—" placeholder. */
function levelish(raw: string | number | null | undefined): string {
  if (raw == null || String(raw).trim() === '' || String(raw) === '—') return '';
  return String(raw);
}

/** Difficulty level as stored ("1" | "2" | "3"), "" when absent. */
export function difficultyValue(q: Question): string {
  return levelish(q.difficulty ?? q.difficulty_level);
}

/**
 * The model's sensitivity before anyone changed it. Falls back to the live
 * value for questions never edited — and for those edited before originals
 * were kept, where nothing better exists.
 */
export function originalSensitivityValue(q: Question): string {
  return levelish(q.provocativeness_original) || sensitivityValue(q);
}

export function originalDifficultyValue(q: Question): string {
  return levelish(q.difficulty_original) || difficultyValue(q);
}

type RatingValue = string | number | null | undefined;

/** Mirror of the server rule: the first real change of a rating keeps the model's value as `<field>_original`. */
function preservedOriginal(existing: RatingValue, before: string, next: RatingValue): RatingValue {
  if (levelish(existing) !== '') return existing;
  if (next === undefined || before === '') return existing;
  return levelish(next) === before ? existing : before;
}

/** Server question overlaid with a staged (unsaved) edit, for display. */
export function applyQuestionEdits(q: Question, e?: Partial<Question>): Question {
  if (!e || !Object.keys(e).length) return q;
  return {
    ...q,
    ...e,
    options: e.options != null ? coerceOptionsLike(q.options, e.options) : q.options,
    provocativeness: e.provocativeness ?? q.provocativeness ?? q.sensitivity_level,
    difficulty: e.difficulty !== undefined ? e.difficulty : q.difficulty ?? q.difficulty_level,
    provocativeness_original: preservedOriginal(q.provocativeness_original, sensitivityValue(q), e.provocativeness),
    difficulty_original: preservedOriginal(q.difficulty_original, difficultyValue(q), e.difficulty),
    excluded_from_export: e.excluded_from_export ?? q.excluded_from_export,
  };
}

const TYPE_ALIASES: Record<string, QuestionKind> = {
  one: 'one',
  one_choice: 'one',
  single: 'one',
  multi: 'multi',
  multiple: 'multi',
  multiple_choice: 'multi',
  open: 'open',
  open_question: 'open',
};

/** Canonical question kind for the aliases the pipeline and exports tolerate. */
export function normalizeQuestionType(value: unknown): QuestionKind | null {
  const key = String(value ?? '').trim().toLowerCase();
  return TYPE_ALIASES[key] ?? null;
}
