import { describe, expect, it } from 'vitest';
import { classifyLegacyError, gateReasonLabel, taskMessageLabel } from './taskMessage';

const t = (key: string, opts?: Record<string, unknown>) => {
  if (key === 'progress:msg.model_auth_failed') return 'Ошибка авторизации при обращении к модели';
  if (key === 'progress:msg.model_output_invalid') return 'Не удалось обработать ответ модели';
  if (key === 'progress:msg.gate_rejected_chunk_not_informative') {
    return 'отсеян: недостаточно информативен';
  }
  if (key === 'progress:msg.gate_rejected_chunk_exercise_block') {
    return 'отсеян: задания и вопросы к параграфу';
  }
  if (key === 'progress:msg.gate_rejected_chunk_front_matter') {
    return 'отсеян: аппарат учебника';
  }
  if (key === 'progress:msg.gate_rejected_generic') {
    return `отсеян: ${opts?.reason ?? ''}`;
  }
  return key;
};

describe('classifyLegacyError', () => {
  it('detects auth failures', () => {
    expect(
      classifyLegacyError('Agent API error: 500: {"detail":"401 Unauthorized"}'),
    ).toBe('model_auth_failed');
  });

  it('detects parse failures', () => {
    expect(
      classifyLegacyError(
        'Agent API error: 500: Failed to parse StructuredQuestionOutput from completion {...}',
      ),
    ).toBe('model_output_invalid');
  });

  it('detects legacy chunk_rejected', () => {
    expect(classifyLegacyError('chunk_rejected: chunk_not_informative')).toBe(
      'gate_rejected_chunk_not_informative',
    );
  });

  it('maps unknown errors to internal_error', () => {
    expect(classifyLegacyError('something went sideways')).toBe('internal_error');
  });
});

describe('taskMessageLabel', () => {
  it('prefers message_code', () => {
    expect(
      taskMessageLabel({ message_code: 'model_auth_failed', error: 'raw' }, t),
    ).toBe('Ошибка авторизации при обращении к модели');
  });

  it('falls back to classifying raw error', () => {
    expect(
      taskMessageLabel(
        { error: 'Agent API error: 500: Failed to parse StructuredQuestionOutput from completion x' },
        t,
      ),
    ).toBe('Не удалось обработать ответ модели');
  });
});

describe('gateReasonLabel', () => {
  it('returns short reason without prefix', () => {
    expect(gateReasonLabel('chunk_not_informative', t)).toBe('недостаточно информативен');
  });

  it('labels the textbook apparatus reasons', () => {
    expect(gateReasonLabel('chunk_exercise_block', t)).toBe('задания и вопросы к параграфу');
    expect(gateReasonLabel('chunk_front_matter', t)).toBe('аппарат учебника');
    expect(taskMessageLabel({ message_code: 'gate_rejected_chunk_exercise_block' } as never, t)).toBe(
      'отсеян: задания и вопросы к параграфу',
    );
  });
});
