import type { QuestionCreateRequest } from '@/shared/types/api-requests';
import type { Question } from '@/shared/types/documents';

export function numericChunkId(chunkId: number | string | undefined): number {
  if (chunkId == null) return 0;
  const n = typeof chunkId === 'number' ? chunkId : parseInt(String(chunkId), 10);
  return Number.isFinite(n) ? n : 0;
}

export function questionOptionsPayload(
  options: Question['options'],
): string | Record<string, string> {
  if (options == null) return '';
  if (typeof options === 'string') return options;
  return options;
}

/** Map a UI Question row to the dataset_api create/update payload shape. */
export function questionToCreateRequest(q: Question): QuestionCreateRequest {
  const prov = q.provocativeness ?? q.sensitivity_level;
  const diff = q.difficulty ?? q.difficulty_level;
  return {
    question_id: q.question_id,
    chunk_id: numericChunkId(q.chunk_id),
    question_type: String(q.question_type ?? ''),
    task: String(q.task ?? ''),
    options: questionOptionsPayload(q.options),
    correct_answer: String(q.correct_answer ?? ''),
    provocativeness: prov == null ? '' : String(prov),
    difficulty: diff == null || String(diff).trim() === '' ? null : String(diff),
    validation_passed:
      q.validation_passed == null ? null : String(q.validation_passed),
    validation_score: q.validation_score ?? null,
    validation_threshold: q.validation_threshold ?? null,
    validation_details:
      q.validation_details == null
        ? null
        : typeof q.validation_details === 'string'
          ? q.validation_details
          : JSON.stringify(q.validation_details),
    validation_justifications:
      q.validation_justifications == null
        ? null
        : typeof q.validation_justifications === 'string'
          ? q.validation_justifications
          : JSON.stringify(q.validation_justifications),
    retry_count: q.retry_count == null ? null : String(q.retry_count),
    source_chunk: q.source_chunk ?? null,
    sensitivity_explanation: q.sensitivity_explanation ?? null,
    difficulty_explanation: q.difficulty_explanation ?? null,
    // The model's pre-edit ratings travel into the new version like the explanations do.
    provocativeness_original: q.provocativeness_original == null ? null : String(q.provocativeness_original),
    difficulty_original: q.difficulty_original == null ? null : String(q.difficulty_original),
    excluded_from_export: q.excluded_from_export ?? null,
    needs_revalidation: q.needs_revalidation ?? null,
    edit_status: q.edit_status ?? null,
    edited_at: q.edited_at ?? null,
    // Carried over so the worker's duplicate guard keeps working on a new version.
    idempotency_key: q.idempotency_key ?? null,
  };
}
