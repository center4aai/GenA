import { qualityState } from '@/shared/lib/revalidation';
import { isExcludedFromExport } from '@/shared/lib/questionEdits';
import {
  difficultyValue,
  normalizeQuestionType,
  sensitivityValue,
  type QuestionKind,
} from '@/shared/lib/questionFields';
import type { Level } from '@/shared/lib/questionStats';
import type { Question } from '@/shared/types/documents';

export type QualityFilter = 'passed' | 'failed' | 'revalidation_required';
/**
 * The "editing status" dimension: the two server statuses plus two UI states a
 * user thinks of alongside them — staged edits and the export switch.
 */
export type EditingFilter = 'user_edited' | 'paraphrased' | 'unsaved' | 'excluded';

/** An empty set means "Все" for that dimension. Dimensions combine with AND. */
export interface QuestionFilterState {
  chunks: Set<string>;
  quality: Set<QualityFilter>;
  types: Set<QuestionKind>;
  sensitivity: Set<Level>;
  difficulty: Set<Level>;
  editing: Set<EditingFilter>;
}

export type FilterKey = keyof QuestionFilterState;
export const FILTER_KEYS: FilterKey[] = ['chunks', 'quality', 'types', 'sensitivity', 'difficulty', 'editing'];

export function emptyFilters(): QuestionFilterState {
  return {
    chunks: new Set(),
    quality: new Set(),
    types: new Set(),
    sensitivity: new Set(),
    difficulty: new Set(),
    editing: new Set(),
  };
}

export function isEmptyFilter(f: QuestionFilterState): boolean {
  return FILTER_KEYS.every((k) => f[k].size === 0);
}

function sameSet(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) if (!b.has(v)) return false;
  return true;
}

export function filtersEqual(a: QuestionFilterState, b: QuestionFilterState): boolean {
  return FILTER_KEYS.every((k) => sameSet(a[k] as Set<string>, b[k] as Set<string>));
}

/** Copy of `f` with one dimension replaced (Sets are treated as immutable). */
export function withFilterValues<K extends FilterKey>(
  f: QuestionFilterState,
  key: K,
  values: Iterable<QuestionFilterState[K] extends Set<infer V> ? V : never>,
): QuestionFilterState {
  return { ...f, [key]: new Set(values) } as QuestionFilterState;
}

/** Distinct chunk ids of the version, numerically sorted, for the "Фрагмент" filter. */
export function chunkFilterOptions(questions: Question[]): string[] {
  const ids = new Set<string>();
  for (const q of questions) {
    const id = q.chunk_id;
    if (id != null && String(id).trim() !== '') ids.add(String(id));
  }
  return [...ids].sort((a, b) => {
    const na = Number(a);
    const nb = Number(b);
    if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb;
    return a.localeCompare(b, undefined, { numeric: true });
  });
}

/** A question with its position in the version, so "Вопрос N" survives filtering and paging. */
export interface IndexedQuestion {
  q: Question;
  index: number;
}

export function indexQuestions(questions: Question[]): IndexedQuestion[] {
  return questions.map((q, index) => ({ q, index }));
}

export interface FilterContext {
  /** question_ids with staged, unsaved edits */
  unsavedIds: Set<string>;
}

function matchesEditing(q: Question, wanted: Set<EditingFilter>, ctx: FilterContext): boolean {
  for (const w of wanted) {
    if (w === 'unsaved') {
      if (q.question_id && ctx.unsavedIds.has(q.question_id)) return true;
    } else if (w === 'excluded') {
      if (isExcludedFromExport(q)) return true;
    } else if (q.edit_status === w) {
      return true;
    }
  }
  return false;
}

/**
 * Filter over the whole version, not the page. Values are read from the
 * server question, never from a draft, so a card does not move between
 * buckets while it is being edited.
 */
export function applyQuestionFilters(
  items: IndexedQuestion[],
  f: QuestionFilterState,
  ctx: FilterContext,
): IndexedQuestion[] {
  if (isEmptyFilter(f)) return items;
  return items.filter(({ q }) => {
    if (f.chunks.size && !f.chunks.has(String(q.chunk_id ?? ''))) return false;
    if (f.quality.size && !f.quality.has(qualityState(q))) return false;
    if (f.types.size) {
      const kind = normalizeQuestionType(q.question_type);
      if (!kind || !f.types.has(kind)) return false;
    }
    if (f.sensitivity.size && !f.sensitivity.has(sensitivityValue(q) as Level)) return false;
    if (f.difficulty.size && !f.difficulty.has(difficultyValue(q) as Level)) return false;
    if (f.editing.size && !matchesEditing(q, f.editing, ctx)) return false;
    return true;
  });
}
