import { agentJson } from './http';
import type { LlmModel, ModelHealth, Question } from '../types/documents';
import type {
  AgentResponse,
} from '../types/api-requests';

export function listModels() {
  return agentJson<LlmModel[]>('/models/');
}

export function listModelsHealth() {
  return agentJson<ModelHealth[]>('/models/health/');
}

export function rephraseQuestions(
  datasetName: string,
  questions: Question[],
  modelId?: string,
  timeoutMs = 300_000,
) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return agentJson<AgentResponse<Question[]>>('/rephrase_questions/', {
    method: 'POST',
    body: JSON.stringify({
      dataset_name: datasetName,
      questions,
      ...(modelId ? { model_id: modelId } : {}),
    }),
    signal: controller.signal,
  }).finally(() => clearTimeout(timer));
}
