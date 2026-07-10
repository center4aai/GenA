export function normalizeId(id: unknown): string {
  if (id == null) return '';
  if (typeof id === 'object' && id !== null && '$oid' in id) {
    return String((id as { $oid: string }).$oid);
  }
  return String(id);
}

export interface DatasetMetadata {
  status?: string;
  queue_name?: string;
  pipeline_mode?: string;
  gate_enabled?: boolean;
  refine_enabled?: boolean;
  ablation_testing?: boolean;
  question_types?: string[];
  total_chunks?: number;
  chunks_passed_gate?: number;
  chunks_rejected?: number;
  total_questions_generated?: number;
  expected_questions?: number;
  generation_model_id?: string;
  validation_model_id?: string;
  last_updated?: string;
  [key: string]: unknown;
}

export interface Question {
  question_id?: string;
  chunk_id?: number | string;
  source_chunk?: string;
  question_type?: string;
  task?: string;
  options?: Record<string, string> | string;
  correct_answer?: string;
  provocativeness?: string | number;
  sensitivity_level?: string | number;
  difficulty?: string | number | null;
  difficulty_level?: string | number | null;
  validation_passed?: boolean | string;
  validation_score?: string;
  validation_threshold?: string;
  validation_details?: unknown;
  validation_justifications?: unknown;
  retry_count?: number | string;
  dataset_name?: string;
  dataset_id?: string;
  created_at?: string;
  dataset_status?: string;
}

export interface Dataset {
  _id: string;
  name: string;
  description?: string;
  source_document?: string;
  questions?: Question[];
  metadata?: DatasetMetadata;
  created_at?: string;
  updated_at?: string;
  current_version?: number;
  requested_version?: number;
  chunks_count?: number;
  chunks_valid?: number;
}

export interface DatasetVersion {
  version: number;
  created_at: string;
}

export interface Task {
  _id: string;
  chunk_id?: number | string;
  chunk_text?: string;
  question_type?: string;
  status?: string;
  priority?: number;
  error?: string;
  result?: unknown;
  queue_name?: string;
  generation_model_id?: string;
  validation_model_id?: string;
  source_document?: string;
  dataset_name?: string;
  dataset_id?: string;
  created_at?: string;
  updated_at?: string;
}

export interface Queue {
  name: string;
  description?: string;
  priority?: number;
  task_count?: number;
  pending_count?: number;
  processing_count?: number;
  completed_count?: number;
  failed_count?: number;
  cancelled_count?: number;
  created_at?: string;
}

export interface GateResult {
  passed?: boolean;
  rejection_reason?: string | null;
  c1_chunk_informative?: number[] | number;
  c2_chunk_reference_clarity?: number[] | number;
  c3_chunk_multi_suitability?: number[] | number;
  c1_confidence?: number;
  c2_confidence?: number;
  c3_confidence?: number;
  c1_reasoning?: string;
  c2_reasoning?: string;
  c3_reasoning?: string;
}

export interface ChunkRecord {
  chunk_index: number;
  chunk_text?: string;
  fragment_data?: Record<string, unknown>;
  gate_result?: GateResult;
  gate_passed?: boolean;
  question_types_valid?: string[];
}

export interface ChunkDetailed {
  fragment_id?: string;
  fragment_data?: {
    combined_text?: string;
    content?: string;
    title?: string;
    hierarchy_context?: Record<string, unknown>;
    [key: string]: unknown;
  };
  hierarchy_context?: Record<string, unknown>;
  _gate_result?: GateResult;
  _gate_passed?: boolean;
  _question_types_valid?: string[];
}

export interface ChunkResponse {
  num_chunks: number;
  chunks?: string[];
  chunks_detailed?: ChunkDetailed[] | string[];
  chunking_method?: string;
  document_type?: { document_name?: string };
}

export interface LlmModel {
  id: string;
  name: string;
}

export interface ModelHealth {
  id: string;
  name: string;
  available: boolean;
}

export type TaskStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';

export interface TaskProgress {
  total: number;
  completed: number;
  failed: number;
  processing: number;
  pending: number;
  cancelled: number;
  status: string;
  progressPct: number;
}

export interface DatasetProgressRow {
  datasetName: string;
  status: string;
  chunksValidTotal: string;
  questionsGenerated: number;
  expectedQuestions: number;
  progressPercent: number;
  created: string;
  lastUpdated: string;
}

export interface QueueStats {
  taskCount: number;
  pendingCount: number;
  processingCount: number;
  completedCount: number;
  failedCount: number;
  cancelledCount: number;
}
