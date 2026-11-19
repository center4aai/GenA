import type { RevalidationRunItem, RevalidationVerdict } from '@/shared/types/documents';

function passedFlag(v: RevalidationVerdict | null): boolean | null {
  const raw = v?.passed;
  if (raw == null) return null;
  const s = String(raw).trim().toLowerCase();
  if (s === 'true') return true;
  if (s === 'false') return false;
  return null;
}

/**
 * One row per question of a revalidation run: the verdict before the run and
 * the one the run's validator gave (comparison of validators on one set).
 */
export function buildRevalidationRunRows(items: RevalidationRunItem[]): Record<string, unknown>[] {
  return items.map((it) => {
    const before = passedFlag(it.previous);
    const after = passedFlag(it.result);
    return {
      question_id: it.question_id,
      question_type: it.question_type,
      task: it.task,
      status: it.status,
      error: it.error,
      previous_model_id: it.previous?.model_id ?? null,
      previous_passed: it.previous?.passed ?? null,
      previous_score: it.previous?.score ?? null,
      new_model_id: it.result?.model_id ?? null,
      new_passed: it.result?.passed ?? null,
      new_score: it.result?.score ?? null,
      verdict_changed: before == null || after == null ? null : before !== after,
      previous_justifications: it.previous?.justifications ?? null,
      new_justifications: it.result?.justifications ?? null,
    };
  });
}
