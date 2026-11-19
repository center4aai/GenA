import type { CollapsibleTone } from '@/shared/ui/CollapsibleSection';
import type {
  RevalidationPrimaryStatus,
  RevalidationScope,
  RevalidationState,
} from '@/shared/types/documents';

export type Translate = (key: string, opts?: Record<string, unknown>) => string;

/** Radio order in the set card (UI ТЗ 6). */
export const REVALIDATION_SCOPES: RevalidationScope[] = ['all', 'required', 'user_edited', 'paraphrased'];

/** Accordion background by the version's main status (UI ТЗ 3). */
export function revalidationTone(status: RevalidationPrimaryStatus): CollapsibleTone {
  switch (status) {
    case 'required':
    case 'completed_with_errors':
      return 'yellow';
    case 'processing':
      return 'blue';
    case 'failed':
      return 'red';
    default:
      return 'green';
  }
}

/** The status part of «Повторная проверка качества | …». */
export function revalidationHeaderStatus(state: RevalidationState, t: Translate): string {
  switch (state.primary_status) {
    case 'processing': {
      const run = state.active_run;
      return t('results:revalStatusProcessing', { x: run?.succeeded ?? 0, y: run?.total ?? 0 });
    }
    case 'required':
      return t('results:revalStatusRequired', { n: state.required_count, m: state.total_count });
    case 'completed':
      return t('results:revalStatusCompleted');
    case 'completed_with_errors':
      return t('results:revalStatusCompletedWithErrors');
    case 'failed':
      return t('results:revalStatusFailed');
    default:
      return t('results:revalStatusNotStarted');
  }
}

/** Whether a run is active for the version (locks editing and AI transform). */
export function isRevalidating(state: RevalidationState | null | undefined): boolean {
  return Boolean(state?.active_run);
}
