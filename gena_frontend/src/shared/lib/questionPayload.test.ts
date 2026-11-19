import { describe, expect, it } from 'vitest';
import { questionToCreateRequest } from './questionPayload';

describe('questionToCreateRequest', () => {
  it('carries the model originals into a new version, null when never kept', () => {
    const kept = questionToCreateRequest({
      question_id: 'q1',
      question_type: 'one',
      task: '?',
      options: { option_1: 'a' },
      correct_answer: '1',
      provocativeness: '3',
      provocativeness_original: 1,
      difficulty: '1',
      difficulty_original: '3',
    });
    expect(kept.provocativeness_original).toBe('1');
    expect(kept.difficulty_original).toBe('3');

    const fresh = questionToCreateRequest({ question_type: 'open', task: '?', correct_answer: 'x', provocativeness: '2' });
    expect(fresh.provocativeness_original).toBeNull();
    expect(fresh.difficulty_original).toBeNull();
  });
});
