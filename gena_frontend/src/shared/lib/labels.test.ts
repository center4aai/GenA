import { describe, expect, it } from 'vitest';
import { humanQueueName } from './labels';

describe('humanQueueName', () => {
  it('strips queue_ prefix and timestamp suffixes', () => {
    expect(
      humanQueueName('queue_Dataset_ГОСТ 34.602-89.docx_20251208_080425_20251208_080433'),
    ).toBe('Dataset_ГОСТ 34.602-89.docx');
  });

  it('keeps readable Russian names', () => {
    expect(humanQueueName('queue_учебник тест 1 — 3 авг. 2026 г._20260803155136')).toBe(
      'учебник тест 1 — 3 авг. 2026 г.',
    );
  });

  it('handles empty input', () => {
    expect(humanQueueName('')).toBe('');
    expect(humanQueueName(null)).toBe('');
  });
});
