
export type DocumentKind = 'normative' | 'textbook' | 'encyclopedia';
/** How the document is split into chunks: rules (headings + regex), layout (PDF fonts), semantic (embeddings), llm (model). */
export type ChunkingMethod = 'rules' | 'layout' | 'semantic' | 'llm';

/** How a question came to differ from what generation produced. */
export type EditStatus = 'user_edited' | 'paraphrased';

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
  document_kind?: DocumentKind;
  chunking_method?: ChunkingMethod;
  /** Engine that actually ran (hierarchical_outline, layout_pdf, …) and why it fell back, if it did. */
  chunking_engine?: string;
  chunking_fallback?: string;
  /** Chunker diagnostics copied from the /chunk/ response (llm windows, paragraph source, layout profile). */
  chunking_stats?: ChunkingStats;
  [key: string]: unknown;
}

/** Per-window statistics of the llm chunking method (chunker/llm_segmentation). */
export interface LlmSegmentationStats {
  model?: string;
  windows?: number;
  llm_ok?: number;
  fallback?: number;
  salvaged_truncated?: number;
  gapfill_segments?: number;
  errors_count?: number;
  llm_seconds?: number;
  [key: string]: unknown;
}

export interface ChunkingStats {
  /** Where the llm method took its paragraphs from: PDF layout or plain text. */
  paragraph_source?: 'layout' | 'text' | string;
  llm_segmentation?: LlmSegmentationStats;
  layout?: Record<string, unknown>;
  semantic?: Record<string, unknown>;
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
  sensitivity_explanation?: string;
  difficulty_explanation?: string;
  /** The model's ratings before the first manual change; set by the server, absent until then. */
  provocativeness_original?: string | number | null;
  difficulty_original?: string | number | null;
  /** Kept in the list with a badge, but left out of every download. */
  excluded_from_export?: boolean;
  idempotency_key?: string;
  needs_revalidation?: boolean;
  /** Whether validation_* describe the current wording (false after an edit or a rephrase). */
  validation_is_current?: boolean;
  /** Model that produced validation_* (missing on old questions → the set's validator). */
  validation_model_id?: string | null;
  /** Revalidation run that last rewrote validation_*. */
  revalidation_run_id?: string | null;
  /** Set by a manual save (user_edited) or a transform rephrase (paraphrased); absent when untouched. */
  edit_status?: EditStatus | null;
  edited_at?: string | null;
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
  questions_count?: number;
  /** Number of versions of the set (list endpoint). */
  versions_count?: number;
  /** Latest manual edit or transform of any version; null when nobody has modified the set. */
  last_user_modified_at?: string | null;
  current_version_user_updated_at?: string | null;
}

/** Full dataset payload from GET /datasets/{id}. */
export interface DatasetDetail extends Dataset {
  dataset_id?: string;
  requested_version?: number;
}

export interface DatasetVersion {
  version: number;
  created_at: string;
  /** Set only by manual edits — generation filling a version is not an update. */
  user_updated_at?: string | null;
  user_updated_by?: string | null;
  questions_count?: number;
  metadata?: DatasetMetadata;
}

export interface Task {
  _id: string;
  chunk_id?: number | string;
  chunk_text?: string;
  question_type?: string;
  task_type?: string;
  question_types?: string[];
  status?: string;
  priority?: number;
  attempts?: number;
  error?: string;
  /** Stable code for user-facing message (errors and gate outcomes). */
  message_code?: string;
  /** Gate verdict stored on the task (false = chunk rejected). */
  gate_passed?: boolean;
  result?: unknown;
  queue_name?: string;
  generation_model_id?: string;
  validation_model_id?: string;
  source_document?: string;
  dataset_name?: string;
  dataset_id?: string;
  document_kind?: DocumentKind;
  created_at?: string;
  updated_at?: string;
}

export interface Queue {
  _id?: string;
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
  c1_chunk_informative?: number[];
  c2_chunk_reference_clarity?: number[];
  c3_chunk_multi_suitability?: number[];
  c1_confidence?: number;
  c2_confidence?: number;
  c3_confidence?: number;
  c1_reasoning?: string;
  c2_reasoning?: string;
  c3_reasoning?: string;
  /** Textbook / encyclopedia gate: content | exercise | front_matter | caption_only. */
  c1_category?: string | null;
}

export interface ChunkRecord {
  _id?: string;
  dataset_id?: string;
  chunk_index: number;
  chunk_text?: string;
  fragment_data?: Record<string, unknown>;
  hierarchy_context?: Record<string, unknown>;
  gate_result?: GateResult;
  gate_passed?: boolean;
  gate_pending?: boolean;
  question_types_valid?: string[];
  created_at?: string;
  updated_at?: string;
}

export interface ChunkDetailed {
  fragment_id?: string;
  fragment_data?: {
    combined_text?: string;
    content?: string;
    title?: string;
    hierarchy_context?: Record<string, unknown>;
    /** Chunker block type: body | list | inset | caption | glossary_entry. */
    block_type?: string;
    page_start?: number | null;
    page_end?: number | null;
    [key: string]: unknown;
  };
  hierarchy_context?: Record<string, unknown>;
  _chunk_index?: number;
  _gate_result?: GateResult;
  _gate_passed?: boolean;
  _question_types_valid?: string[];
}

export interface ChunkResponse {
  filename?: string;
  file_type?: string;
  num_chunks: number;
  chunks?: string[];
  chunks_detailed?: ChunkDetailed[] | string[];
  chunking_method?: string;
  min_size?: number;
  document_type?: { document_name?: string };
}

export interface LlmModel {
  id: string;
  name: string;
  base_url?: string;
  model_name?: string;
  provider?: string;
  available?: boolean;
  served_models?: string[];
}

export interface ModelHealth {
  id: string;
  name: string;
  available: boolean;
  /** Why the model is down (not served, probe failed); only when `available` is false. */
  reason?: string;
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

export type PipelineStatsPhaseState =
  | 'completed'
  | 'active'
  | 'pending'
  | 'failed'
  | 'disabled';

export interface PipelineStatsMeta {
  /** {type: {count, chars}} the chunker threw away (exercises, front matter, debris). */
  chunking_dropped_blocks?: Record<string, { count: number; chars: number }>;
  pipeline_mode?: string;
  gate_enabled?: boolean;
  ablation_testing?: boolean;
  question_types?: string[];
  queue_name?: string;
  status?: string;
  experiment_source_document?: string;
  refine_enabled?: boolean;
}

export interface PipelineGateCriteria {
  c1_pass: number;
  c2_pass: number;
  c3_pass: number;
  c1_avg_confidence: number | null;
  c2_avg_confidence: number | null;
  c3_avg_confidence: number | null;
  scored: number;
}

export interface PipelineStats {
  dataset_id: string;
  dataset_name?: string;
  meta: PipelineStatsMeta;
  chunking: {
    state: PipelineStatsPhaseState;
    total_chunks: number;
    done: number;
    total: number;
    by_status?: Record<string, number>;
  };
  gate: {
    state: PipelineStatsPhaseState;
    total: number;
    done: number;
    passed: number;
    rejected: number;
    rejection_reasons: Record<string, number>;
    /** Gate outcome per chunker block type (body / inset / caption / …). */
    by_block_type?: Record<string, { total: number; passed: number; rejected: number; pending: number }>;
    criteria: PipelineGateCriteria;
    question_types_valid: Record<string, number>;
    by_status?: Record<string, number>;
    tasks_total?: number;
  };
  generation: {
    state: PipelineStatsPhaseState;
    total: number;
    /** Tasks that stopped moving, failures included — decides when the phase ends. */
    done: number;
    /** Tasks that produced a question. This is the number the progress UI shows. */
    completed?: number;
    by_status: Record<string, number>;
    questions_generated: number;
    expected_questions: number;
    validation: {
      passed: number;
      failed: number;
      avg_score: number | null;
    };
  };
  /** Model ids the run's tasks reference, so the page need not scan them itself. */
  models?: {
    generation: string[];
    validation: string[];
  };
}

/** Quality revalidation of one version (GET /datasets/{id}/versions/{v}/revalidation). */
export type RevalidationScope = 'all' | 'required' | 'user_edited' | 'paraphrased';
export type RevalidationRunStatus = 'processing' | 'completed' | 'completed_with_errors' | 'failed';
export type RevalidationPrimaryStatus =
  | 'not_started'
  | 'required'
  | 'processing'
  | 'completed'
  | 'completed_with_errors'
  | 'failed';

export interface RevalidationRun {
  run_id: string;
  status: RevalidationRunStatus;
  scope: RevalidationScope;
  total: number;
  succeeded: number;
  errors: number;
  created_at?: string | null;
  finished_at?: string | null;
  created_by?: string | null;
  /** Validator of the run (absent on runs made before the model choice). */
  validation_model_id?: string | null;
}

/** One question's verdict before/after a run (comparison export). */
export interface RevalidationVerdict {
  passed: string | null;
  score: string | null;
  threshold: string | null;
  justifications: string | null;
  model_id: string | null;
}

export interface RevalidationRunItem {
  question_id: string;
  question_type: string | null;
  task: string | null;
  status: 'pending' | 'processing' | 'done' | 'error';
  error: string | null;
  previous: RevalidationVerdict | null;
  result: RevalidationVerdict | null;
}

export interface RevalidationRunItems {
  run: RevalidationRun;
  items: RevalidationRunItem[];
}

export interface RevalidationState {
  dataset_id: string;
  version: number;
  available: boolean;
  primary_status: RevalidationPrimaryStatus;
  required_count: number;
  total_count: number;
  scope_counts: Record<RevalidationScope, number>;
  active_run: RevalidationRun | null;
  last_run: RevalidationRun | null;
  retryable_count: number;
}
