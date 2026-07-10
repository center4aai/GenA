import { agentJson } from './http';
import type { GateResult, LlmModel, ModelHealth, Question } from '../types/documents';

export function listModels() {
  return agentJson<LlmModel[]>('/models/');
}

export function listModelsHealth() {
  return agentJson<ModelHealth[]>('/models/health/');
}

export function runChunkGate(payload: {
  chunk: string;
  question_type: string;
  validation_model_id?: string;
}) {
  return agentJson<{ result: GateResult }>('/chunk_gate/', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function processPrompt(payload: Record<string, unknown>) {
  return agentJson<{ result: { output: Record<string, unknown> } }>('/process_prompt/', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function rephraseQuestions(
  datasetName: string,
  questions: Question[],
  timeoutMs = 300_000,
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return agentJson<{ status: string; result?: Question[] }>('/rephrase_questions/', {
    method: 'POST',
    body: JSON.stringify({ dataset_name: datasetName, questions }),
    signal: controller.signal,
  }).finally(() => clearTimeout(timer));
}
