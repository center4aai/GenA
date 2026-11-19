import type { Task } from '@/shared/types/documents';

export type Translate = (key: string, opts?: Record<string, unknown>) => string;

/** Known message_code values written by the worker. */
export const MESSAGE_CODE_KEYS: Record<string, string> = {
  model_auth_failed: 'progress:msg.model_auth_failed',
  model_output_invalid: 'progress:msg.model_output_invalid',
  model_unavailable: 'progress:msg.model_unavailable',
  model_error: 'progress:msg.model_error',
  chunk_too_short: 'progress:msg.chunk_too_short',
  question_save_failed: 'progress:msg.question_save_failed',
  task_stuck: 'progress:msg.task_stuck',
  document_fetch_failed: 'progress:msg.document_fetch_failed',
  chunker_failed: 'progress:msg.chunker_failed',
  chunker_method_unavailable: 'progress:msg.chunker_method_unavailable',
  no_usable_chunks: 'progress:msg.no_usable_chunks',
  chunks_save_failed: 'progress:msg.chunks_save_failed',
  enqueue_failed: 'progress:msg.enqueue_failed',
  gate_unavailable: 'progress:msg.gate_unavailable',
  gate_timeout: 'progress:msg.gate_timeout',
  gate_overloaded: 'progress:msg.gate_overloaded',
  internal_error: 'progress:msg.internal_error',
  gate_rejected_chunk_not_informative: 'progress:msg.gate_rejected_chunk_not_informative',
  gate_rejected_chunk_not_self_contained: 'progress:msg.gate_rejected_chunk_not_self_contained',
  gate_rejected_chunk_not_suitable_for_multi: 'progress:msg.gate_rejected_chunk_not_suitable_for_multi',
  gate_rejected_chunk_exercise_block: 'progress:msg.gate_rejected_chunk_exercise_block',
  gate_rejected_chunk_front_matter: 'progress:msg.gate_rejected_chunk_front_matter',
  gate_passed_chunk_not_suitable_for_multi: 'progress:msg.gate_passed_chunk_not_suitable_for_multi',
  gate_rejected_gate_llm_error: 'progress:msg.gate_rejected_gate_llm_error',
  gate_rejected_gate_parse_error: 'progress:msg.gate_rejected_gate_parse_error',
  gate_rejected_gate_chain_not_configured: 'progress:msg.gate_rejected_gate_chain_not_configured',
  gate_rejected_unknown: 'progress:msg.gate_rejected_unknown',
};

const GATE_REASON_KEYS: Record<string, string> = {
  chunk_not_informative: 'progress:msg.gate_rejected_chunk_not_informative',
  chunk_not_self_contained: 'progress:msg.gate_rejected_chunk_not_self_contained',
  chunk_not_suitable_for_multi: 'progress:msg.gate_rejected_chunk_not_suitable_for_multi',
  chunk_exercise_block: 'progress:msg.gate_rejected_chunk_exercise_block',
  chunk_front_matter: 'progress:msg.gate_rejected_chunk_front_matter',
  gate_llm_error: 'progress:msg.gate_rejected_gate_llm_error',
  gate_parse_error: 'progress:msg.gate_rejected_gate_parse_error',
  gate_chain_not_configured: 'progress:msg.gate_rejected_gate_chain_not_configured',
  unknown: 'progress:msg.gate_rejected_unknown',
};

/** Short human labels for gate rejection_reason codes (chunk badges / reason list). */
export function gateReasonLabel(reason: string | null | undefined, t: Translate): string {
  if (!reason) return '';
  const key = GATE_REASON_KEYS[reason];
  if (key) {
    // Strip the "отсеян: " / "rejected: " prefix for badge context.
    const full = t(key);
    const sep = full.indexOf(':');
    return sep >= 0 ? full.slice(sep + 1).trim() : full;
  }
  return reason;
}

/** Classify a legacy raw error string the same way the worker does. */
export function classifyLegacyError(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const text = raw.toLowerCase();
  if (text.includes('401') || text.includes('unauthorized') || text.includes('authentication')) {
    return 'model_auth_failed';
  }
  if (text.includes('failed to parse structuredquestionoutput') || text.includes('from completion')) {
    return 'model_output_invalid';
  }
  if (text.includes('force-failed after') || text.includes('stuck recover')) {
    return 'task_stuck';
  }
  if (text.includes('empty or too short')) return 'chunk_too_short';
  if (text.includes('failed to save question')) return 'question_save_failed';
  if (text.includes('fetch document failed')) return 'document_fetch_failed';
  if (text.includes('chunker error') || text.includes('chunker request failed')) {
    return 'chunker_failed';
  }
  if (text.includes('no usable chunks')) return 'no_usable_chunks';
  if (text.includes('save chunks failed')) return 'chunks_save_failed';
  if (text.includes('enqueue') && text.includes('failed')) return 'enqueue_failed';
  if (
    (text.includes('gate api error') || text.includes('gate request failed'))
    && (text.includes('timeout') || text.includes('timed out'))
  ) {
    return 'gate_timeout';
  }
  if (text.includes('gate llm busy')) return 'gate_overloaded';
  if (
    text.includes('gate api error')
    || text.includes('gate request failed')
    || text.includes('gate processing error')
  ) {
    return 'gate_unavailable';
  }
  if (
    text.includes('agent request failed')
    || text.includes('timeout')
    || text.includes('connection')
  ) {
    return 'model_unavailable';
  }
  if (text.includes('agent api error')) return 'model_error';
  if (text.startsWith('chunk_rejected:')) {
    const reason = raw.split(':', 2)[1]?.trim() || 'unknown';
    return `gate_rejected_${reason}`;
  }
  if (text.includes('processing error') || text.includes('chunk task missing')) {
    return 'internal_error';
  }
  return 'internal_error';
}

function resolveMessageCode(task: Pick<Task, 'message_code' | 'error' | 'gate_passed' | 'status'>): string | null {
  if (task.message_code) return task.message_code;
  if (task.gate_passed === false) {
    const fromError = classifyLegacyError(task.error);
    if (fromError?.startsWith('gate_rejected_')) return fromError;
    return 'gate_rejected_unknown';
  }
  // Legacy skipped gate rejection.
  if (task.status === 'skipped') {
    const fromError = classifyLegacyError(task.error);
    if (fromError) return fromError;
  }
  return classifyLegacyError(task.error);
}

/**
 * User-facing message for a task (Message column / failed accordion).
 * Prefers message_code; falls back to classifying the raw error for old tasks.
 */
export function taskMessageLabel(
  task: Pick<Task, 'message_code' | 'error' | 'gate_passed' | 'status'>,
  t: Translate,
): string {
  const code = resolveMessageCode(task);
  if (code) {
    const key = MESSAGE_CODE_KEYS[code] ?? `progress:msg.${code}`;
    const translated = t(key);
    // i18next returns the key when missing; prefer a soft fallback.
    if (translated && translated !== key) return translated;
    if (code.startsWith('gate_rejected_')) {
      const reason = code.slice('gate_rejected_'.length);
      return t('progress:msg.gate_rejected_generic', { reason });
    }
  }
  return '';
}
