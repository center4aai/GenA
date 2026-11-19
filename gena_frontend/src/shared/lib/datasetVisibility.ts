import type { Dataset } from '@/shared/types/documents';

/**
 * Runs that ended without usable output: the validator or gate service failed,
 * or the gate rejected every chunk. Codes mirror dataset_api/pipeline_constants.py.
 */
const TERMINAL_FAILURE_STATUSES = new Set(['failed', 'no_valid_chunks']);
const IN_FLIGHT_STATUSES = new Set(['pending', 'processing', 'in_progress']);

export type EditorDatasetStatus = 'completed' | 'processing';

export function datasetStatusCode(ds: Dataset): string {
  return String(ds.metadata?.status ?? '').trim().toLowerCase();
}

/** Question count of the current version, from whichever field the list carries. */
export function datasetQuestionCount(ds: Dataset): number {
  const n = ds.questions_count ?? ds.metadata?.total_questions_generated ?? ds.questions?.length;
  return typeof n === 'number' && Number.isFinite(n) ? n : 0;
}

/**
 * My questions cannot fix a broken generation, so sets whose run failed
 * critically are not listed there at all: failed / no valid chunks, or a
 * finished run that produced nothing. A run still in flight stays visible with
 * zero questions — every generation starts that way.
 */
export function isDatasetVisibleInEditor(ds: Dataset): boolean {
  const status = datasetStatusCode(ds);
  if (TERMINAL_FAILURE_STATUSES.has(status)) return false;
  if (datasetQuestionCount(ds) > 0) return true;
  return IN_FLIGHT_STATUSES.has(status);
}

/** The two statuses the editor shows: partial failures are still a usable set. */
export function datasetEditorStatus(ds: Dataset): EditorDatasetStatus {
  return IN_FLIGHT_STATUSES.has(datasetStatusCode(ds)) ? 'processing' : 'completed';
}
