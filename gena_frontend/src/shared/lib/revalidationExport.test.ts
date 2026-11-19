import { describe, expect, it } from 'vitest';
import { buildRevalidationRunRows } from './revalidationExport';

const verdict = (passed: string | null, model: string, score = '18/20.5') => ({
  passed, score, threshold: '18.0', justifications: '{}', model_id: model,
});

describe('buildRevalidationRunRows', () => {
  it('puts the previous and the new verdict side by side', () => {
    const rows = buildRevalidationRunRows([
      { question_id: 'q1', question_type: 'one', task: 'Т?', status: 'done', error: null,
        previous: verdict('True', 'qwen'), result: verdict('False', 'gemma', '12/20.5') },
      { question_id: 'q2', question_type: 'open', task: 'О?', status: 'done', error: null,
        previous: verdict('True', 'qwen'), result: verdict('True', 'gemma') },
      { question_id: 'q3', question_type: 'multi', task: 'М?', status: 'error', error: 'llm unavailable',
        previous: verdict('False', 'qwen'), result: null },
    ]);
    expect(rows[0]).toMatchObject({
      previous_model_id: 'qwen', previous_passed: 'True', new_model_id: 'gemma',
      new_passed: 'False', new_score: '12/20.5', verdict_changed: true,
    });
    expect(rows[1].verdict_changed).toBe(false);
    expect(rows[2]).toMatchObject({ status: 'error', error: 'llm unavailable', new_passed: null, verdict_changed: null });
  });
});
