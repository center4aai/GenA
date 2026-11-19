import { describe, expect, it } from 'vitest';
import type { RevalidationState } from '@/shared/types/documents';
import { isRevalidating, revalidationHeaderStatus, revalidationTone } from './revalidationState';

const t = (key: string, opts?: Record<string, unknown>) => `${key}${opts ? JSON.stringify(opts) : ''}`;

function state(partial: Partial<RevalidationState>): RevalidationState {
  return {
    dataset_id: 'd',
    version: 2,
    available: true,
    primary_status: 'not_started',
    required_count: 0,
    total_count: 100,
    scope_counts: { all: 100, required: 0, user_edited: 0, paraphrased: 0 },
    active_run: null,
    last_run: null,
    retryable_count: 0,
    ...partial,
  };
}

describe('revalidation state helpers', () => {
  it('colours the accordion by the main status', () => {
    expect(revalidationTone('not_started')).toBe('green');
    expect(revalidationTone('completed')).toBe('green');
    expect(revalidationTone('required')).toBe('yellow');
    expect(revalidationTone('completed_with_errors')).toBe('yellow');
    expect(revalidationTone('processing')).toBe('blue');
    expect(revalidationTone('failed')).toBe('red');
  });

  it('renders the header status with counts', () => {
    expect(revalidationHeaderStatus(state({ primary_status: 'required', required_count: 20 }), t)).toBe(
      'results:revalStatusRequired{"n":20,"m":100}',
    );
    const run = { run_id: 'r', status: 'processing' as const, scope: 'all' as const, total: 20, succeeded: 17, errors: 0 };
    const processing = state({ primary_status: 'processing', active_run: run });
    expect(revalidationHeaderStatus(processing, t)).toBe('results:revalStatusProcessing{"x":17,"y":20}');
    expect(isRevalidating(processing)).toBe(true);
    expect(isRevalidating(state({}))).toBe(false);
    expect(isRevalidating(null)).toBe(false);
  });
});
