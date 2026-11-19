import { describe, expect, it } from 'vitest';
import {
  applyQuestionFilters,
  chunkFilterOptions,
  emptyFilters,
  filtersEqual,
  indexQuestions,
  isEmptyFilter,
  withFilterValues,
} from './questionFilters';
import type { Question } from '../types/documents';

const QS: Question[] = [
  { question_id: 'a', chunk_id: 2, question_type: 'one', provocativeness: '1', difficulty: '1', validation_passed: 'True' },
  { question_id: 'b', chunk_id: 10, question_type: 'multi', provocativeness: '2', difficulty: '3', validation_passed: 'False', edit_status: 'user_edited' },
  { question_id: 'c', chunk_id: 2, question_type: 'open', provocativeness: '3', validation_passed: 'True', edit_status: 'paraphrased' },
  { question_id: 'd', chunk_id: 1, question_type: 'open', provocativeness: '' },
  { question_id: 'e', chunk_id: 1, question_type: 'one', provocativeness: '2', excluded_from_export: true },
];
const ITEMS = indexQuestions(QS);
const CTX = { unsavedIds: new Set(['d']) };
const ids = (items: ReturnType<typeof indexQuestions>) => items.map((i) => i.q.question_id);

describe('filter state helpers', () => {
  it('starts empty and compares by value', () => {
    expect(isEmptyFilter(emptyFilters())).toBe(true);
    const a = withFilterValues(emptyFilters(), 'types', ['one', 'open']);
    const b = withFilterValues(emptyFilters(), 'types', ['open', 'one']);
    expect(filtersEqual(a, b)).toBe(true);
    expect(filtersEqual(a, emptyFilters())).toBe(false);
    expect(isEmptyFilter(a)).toBe(false);
  });

  it('lists distinct chunk ids in numeric order', () => {
    expect(chunkFilterOptions(QS)).toEqual(['1', '2', '10']);
  });
});

describe('applyQuestionFilters', () => {
  it('returns everything, indices intact, when no filter is set', () => {
    const out = applyQuestionFilters(ITEMS, emptyFilters(), CTX);
    expect(out).toBe(ITEMS);
    expect(out.map((i) => i.index)).toEqual([0, 1, 2, 3, 4]);
  });

  it('filters each dimension and keeps the original index', () => {
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'chunks', ['2']), CTX))).toEqual(['a', 'c']);
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'quality', ['failed']), CTX))).toEqual(['b']);
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'types', ['open']), CTX))).toEqual(['c', 'd']);
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'sensitivity', ['1', '3']), CTX))).toEqual(['a', 'c']);
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'types', ['one']), CTX))).toEqual(['a', 'e']);
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'difficulty', ['3']), CTX))).toEqual(['b']);
    const open = applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'types', ['open']), CTX);
    expect(open.map((i) => i.index)).toEqual([2, 3]);
  });

  it('drops questions without a verdict when quality is filtered', () => {
    const out = applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'quality', ['passed', 'failed']), CTX);
    expect(ids(out)).toEqual(['a', 'b', 'c']);
  });

  it('buckets stale verdicts as "revalidation required", never as passed/failed', () => {
    const stale = indexQuestions([
      ...QS,
      { question_id: 'f', chunk_id: 3, question_type: 'one', validation_passed: 'True', validation_is_current: false },
    ]);
    expect(ids(applyQuestionFilters(stale, withFilterValues(emptyFilters(), 'quality', ['revalidation_required']), CTX))).toEqual(['d', 'e', 'f']);
    expect(ids(applyQuestionFilters(stale, withFilterValues(emptyFilters(), 'quality', ['passed']), CTX))).toEqual(['a', 'c']);
  });

  it('editing status ORs within the dimension and reads unsaved ids from context', () => {
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'editing', ['user_edited', 'paraphrased']), CTX))).toEqual(['b', 'c']);
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'editing', ['unsaved']), CTX))).toEqual(['d']);
  });

  it('treats "excluded from export" as one more editing-status bucket', () => {
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'editing', ['excluded']), CTX))).toEqual(['e']);
    expect(ids(applyQuestionFilters(ITEMS, withFilterValues(emptyFilters(), 'editing', ['excluded', 'paraphrased']), CTX))).toEqual(['c', 'e']);
  });

  it('ANDs across dimensions', () => {
    let f = withFilterValues(emptyFilters(), 'chunks', ['2']);
    f = withFilterValues(f, 'types', ['open']);
    expect(ids(applyQuestionFilters(ITEMS, f, CTX))).toEqual(['c']);
  });
});
