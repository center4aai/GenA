import { coerceOptionsLike } from '@/shared/lib/optionsFormat';
import { hasRealQuestionId } from '@/shared/lib/questionFields';
import { questionOptionsPayload } from '@/shared/lib/questionPayload';
import { editNeedsRevalidation } from '@/shared/lib/revalidation';
import type { QuestionPatchRequest } from '@/shared/types/api-requests';
import type { Question } from '@/shared/types/documents';

/** Excluded questions stay in the list with a badge but leave every download. */
export function isExcludedFromExport(q: Question): boolean {
  return Boolean(q.excluded_from_export);
}

/**
 * Edit status a transformed question carries into the new version.
 *
 * Only a reworded task counts as a paraphrase: shuffle rewrites options and
 * correct_answer and must not be mistaken for one. An untouched task keeps the
 * status the question already had, so an earlier manual edit survives a
 * shuffle-only transform. Manual edits themselves are stamped by the server.
 */
export function withTransformEditStatus(
  original: Question,
  transformed: Question,
  now: Date = new Date(),
): Question {
  const before = String(original.task ?? '').trim();
  const after = String(transformed.task ?? '').trim();
  if (before !== after) {
    return { ...transformed, edit_status: 'paraphrased', edited_at: now.toISOString() };
  }
  return {
    ...transformed,
    edit_status: original.edit_status ?? transformed.edit_status ?? null,
    edited_at: original.edited_at ?? transformed.edited_at ?? null,
  };
}

/**
 * The one-field PATCH behind the include-in-export switch. Null when the
 * question has no id the backend can address (it would answer 409).
 */
export function exportTogglePatch(q: Question, excluded: boolean): QuestionPatchRequest | null {
  if (!hasRealQuestionId(q)) return null;
  return { question_id: String(q.question_id), excluded_from_export: excluded };
}

/**
 * Turn staged edits into the minimal PATCH body: only the questions that
 * changed, and within them only the fields that changed.
 *
 * Sending the whole set back is what used to force a new version on every save
 * and what dropped questions generated while the page was open.
 */
export function buildQuestionPatches(
  questions: Question[],
  edits: Record<string, Partial<Question>>,
): QuestionPatchRequest[] {
  const patches: QuestionPatchRequest[] = [];
  for (const q of questions) {
    const qid = q.question_id;
    const edit = qid ? edits[qid] : undefined;
    if (!qid || !edit || !Object.keys(edit).length) continue;

    const patch: QuestionPatchRequest = { question_id: qid };
    if (edit.question_type !== undefined) patch.question_type = String(edit.question_type ?? '');
    if (edit.task !== undefined) patch.task = String(edit.task ?? '');
    if (edit.options !== undefined) {
      patch.options = questionOptionsPayload(coerceOptionsLike(q.options, edit.options));
    }
    if (edit.correct_answer !== undefined) patch.correct_answer = String(edit.correct_answer ?? '');
    if (edit.provocativeness !== undefined) {
      patch.provocativeness = edit.provocativeness == null ? '' : String(edit.provocativeness);
    }
    if (edit.difficulty !== undefined) {
      // The picker's "no value" entry is an em dash; store it as a real null.
      const raw = edit.difficulty == null ? '' : String(edit.difficulty).trim();
      patch.difficulty = raw === '' || raw === '—' ? null : raw;
    }
    if (edit.excluded_from_export !== undefined) {
      patch.excluded_from_export = Boolean(edit.excluded_from_export);
    }
    if (editNeedsRevalidation(edit)) {
      // The server marks the verdict stale (validation_is_current=false) and
      // keeps it stored; the scores are no longer wiped on edit.
      patch.needs_revalidation = true;
    }
    patches.push(patch);
  }
  return patches;
}
