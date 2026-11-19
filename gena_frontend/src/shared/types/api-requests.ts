/** Request body types aligned with dataset_api / agent_api OpenAPI models. */

import type { EditStatus, GateResult } from './documents';

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

/** Creates a new version — used by transform only. */
export interface DatasetUpdateRequest {
  questions: QuestionCreateRequest[];
  metadata?: Record<string, unknown>;
  base_version: number;
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
  idempotency_key?: string | null;
  sensitivity_explanation?: string | null;
  difficulty_explanation?: string | null;
  provocativeness_original?: string | null;
  difficulty_original?: string | null;
  excluded_from_export?: boolean | null;
  needs_revalidation?: boolean | null;
  edit_status?: EditStatus | null;
  edited_at?: string | null;
}

/** One question's in-place edit; only the listed fields may be changed. */
export interface QuestionPatchRequest {
  question_id: string;
  question_type?: string;
  task?: string;
  options?: string | Record<string, string>;
  correct_answer?: string;
  provocativeness?: string;
  difficulty?: string | null;
  excluded_from_export?: boolean;
  needs_revalidation?: boolean;
  validation_passed?: string | null;
  validation_score?: string | null;
  validation_threshold?: string | null;
  validation_details?: string | null;
  validation_justifications?: string | null;
}

export interface QuestionsPatchResponse {
  dataset_id: string;
  version: number;
  questions_updated: number;
  /** True when wording or ratings changed; an export toggle alone is not an edit. */
  content_edited?: boolean;
  /** Null when the patch was not a content edit (nothing was stamped). */
  user_updated_at: string | null;
  message: string;
}

export interface ChunkCreateRequest {
  chunk_index: number;
  chunk_text: string;
  fragment_data?: Record<string, unknown> | null;
  hierarchy_context?: Record<string, unknown> | null;
  gate_result?: GateResult | Record<string, unknown> | null;
  gate_passed?: boolean;
  gate_pending?: boolean;
  question_types_valid?: string[];
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
  /**
   * "generation" (default), "gate", or "chunk". A "chunk" task runs document
   * chunking in the queue (worker downloads the raw file, calls the chunker,
   * then fans out gate/generation tasks) so the browser never blocks.
   */
  task_type?: 'generation' | 'gate' | 'chunk';
  /** For gate/chunk tasks: requested question types the verdict applies to. */
  question_types?: string[];
  /** Pipeline flavour: normative (legal), textbook, or encyclopedia. */
  document_kind?: 'normative' | 'textbook' | 'encyclopedia';
  /** For chunk tasks: rules | layout | semantic | llm (chunker CHUNKING_METHODS). */
  chunking_method?: 'rules' | 'layout' | 'semantic' | 'llm';
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
  document_kind?: 'normative' | 'textbook' | 'encyclopedia';
}

export interface RephraseQuestionsRequest {
  dataset_name: string;
  questions: unknown[];
  model_id?: string;
}

/** agent_api ResponseModel wrapper. */
export interface AgentResponse<T = unknown> {
  status: string;
  result?: T;
  error?: string;
}

export interface PromptProcessResult {
  question_type?: string;
  pipeline_mode?: string;
  output?: Record<string, unknown>;
}

export interface LoginResponse {
  access_token?: string;
  token?: string;
  token_type?: string;
  role?: string;
}

export interface DatasetCreateResponse {
  dataset_id: string;
  name?: string;
  version?: number;
  message?: string;
}

export interface DatasetUpdateResponse {
  dataset_id: string;
  new_version: number;
  message?: string;
}

export interface ChunksSaveResponse {
  dataset_id: string;
  chunks_saved: number;
  message?: string;
}

export interface QueueTasksAddResponse {
  queue_name?: string;
  tasks_added: number;
  task_ids?: string[];
  message?: string;
}
