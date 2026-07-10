/** Request body types aligned with dataset_api / agent_api OpenAPI models. */

export interface UserLoginRequest {
  username: string;
  password: string;
}

export interface DatasetCreateRequest {
  name: string;
  description?: string;
  source_document: string;
  questions: QuestionCreateRequest[];
  metadata?: Record<string, unknown>;
}

export interface DatasetUpdateRequest {
  questions: QuestionCreateRequest[];
  metadata?: Record<string, unknown>;
}

export interface QuestionCreateRequest {
  question_id?: string;
  chunk_id: number;
  question_type: string;
  task: string;
  options: string | Record<string, string>;
  correct_answer: string;
  provocativeness: string;
  difficulty?: string | null;
  validation_passed?: string | null;
  validation_score?: string | null;
  validation_threshold?: string | null;
  validation_details?: string | null;
  validation_justifications?: string | null;
  retry_count?: string | null;
  source_chunk?: string | null;
}

export interface TaskCreateRequest {
  chunk_id: number;
  chunk_text: string;
  question_type: string;
  source_document: string;
  dataset_name: string;
  dataset_id?: string;
  dataset_description?: string;
  priority?: number;
  generation_model_id?: string;
  validation_model_id?: string;
  chunk_pre_validated?: boolean;
  pipeline_mode?: string;
}

export interface QueueCreateRequest {
  name: string;
  description?: string;
  priority?: number;
}

export interface ChunkGateRequest {
  chunk: string;
  question_type: string;
  validation_model_id?: string;
}

export interface ChunkGateBatchRequest {
  items: Array<{ chunk_id: number | string; chunk: string; question_type: string }>;
  validation_model_id?: string;
}

export interface PromptRequest {
  prompt: string;
  question_type: string;
  source: string;
  chat_id: number;
  source_text?: string;
  additional_params?: Record<string, unknown>;
  generation_model_id?: string;
  validation_model_id?: string;
  chunk_pre_validated?: boolean;
  pipeline_mode?: string;
}

export interface RephraseQuestionsRequest {
  dataset_name: string;
  questions: unknown[];
  model_id?: string;
}

export interface PaginatedDatasetsResponse<T = unknown> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}
