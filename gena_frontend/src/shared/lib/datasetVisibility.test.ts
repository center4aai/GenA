import { describe, expect, it } from 'vitest';
import {
  datasetEditorStatus,
  datasetQuestionCount,
  isDatasetVisibleInEditor,
} from './datasetVisibility';
import type { Dataset } from '../types/documents';

function ds(status: string | undefined, questions: number, extra: Partial<Dataset> = {}): Dataset {
  return { _id: 'd', name: 'D', questions_count: questions, metadata: { status }, ...extra };
}

describe('isDatasetVisibleInEditor', () => {
  it('hides runs that failed critically whatever they produced', () => {
    expect(isDatasetVisibleInEditor(ds('failed', 12))).toBe(false);
    expect(isDatasetVisibleInEditor(ds('no_valid_chunks', 0))).toBe(false);
  });

  it('hides finished runs that produced nothing', () => {
    expect(isDatasetVisibleInEditor(ds('completed', 0))).toBe(false);
    expect(isDatasetVisibleInEditor(ds('completed_with_failures', 0))).toBe(false);
    expect(isDatasetVisibleInEditor(ds('cancelled', 0))).toBe(false);
    expect(isDatasetVisibleInEditor(ds(undefined, 0))).toBe(false);
  });

  it('shows any set with questions, partial failures included', () => {
    expect(isDatasetVisibleInEditor(ds('completed', 5))).toBe(true);
    expect(isDatasetVisibleInEditor(ds('completed_with_failures', 5))).toBe(true);
    expect(isDatasetVisibleInEditor(ds('cancelled', 3))).toBe(true);
  });

  it('keeps a generation in flight visible before its first question', () => {
    expect(isDatasetVisibleInEditor(ds('processing', 0))).toBe(true);
    expect(isDatasetVisibleInEditor(ds('pending', 0))).toBe(true);
  });
});

describe('datasetQuestionCount', () => {
  it('prefers the live count, then metadata, then the embedded array', () => {
    expect(datasetQuestionCount(ds('completed', 4))).toBe(4);
    expect(
      datasetQuestionCount({ _id: 'd', name: 'D', metadata: { total_questions_generated: 9 } }),
    ).toBe(9);
    expect(datasetQuestionCount({ _id: 'd', name: 'D', questions: [{}, {}] })).toBe(2);
    expect(datasetQuestionCount({ _id: 'd', name: 'D' })).toBe(0);
  });
});

describe('datasetEditorStatus', () => {
  it('collapses every status to processing or completed', () => {
    expect(datasetEditorStatus(ds('processing', 0))).toBe('processing');
    expect(datasetEditorStatus(ds('in_progress', 2))).toBe('processing');
    expect(datasetEditorStatus(ds('completed', 2))).toBe('completed');
    expect(datasetEditorStatus(ds('completed_with_failures', 2))).toBe('completed');
  });
});
