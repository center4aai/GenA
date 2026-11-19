import { datasetFetch, datasetJson, HttpError, parseErrorBody } from './http';
import type {
  ChunkRecord,
  Dataset,
  DatasetDetail,
  DatasetVersion,
  PipelineStats,
  Queue,
  RevalidationRunItems,
  RevalidationScope,
  RevalidationState,
  Task,
} from '../types/documents';
import type {
  DatasetCreateRequest,
  DatasetCreateResponse,
  DatasetUpdateRequest,
  DatasetUpdateResponse,
  LoginResponse,
  QueueCreateRequest,
  QueueTasksAddResponse,
  QuestionPatchRequest,
  QuestionsPatchResponse,
  TaskCreateRequest,
} from '../types/api-requests';

/** Encode a single path segment (ids, queue names) so `/`, `#`, `?` survive the round trip. */
function seg(x: string | number): string {
  return encodeURIComponent(String(x));
}

/** Backend returns a plain array from GET /datasets/ (not paginated). */
export async function listDatasets(opts?: { mine?: boolean }): Promise<Dataset[]> {
  const path = opts?.mine ? '/datasets/?mine=1' : '/datasets/';
  const data = await datasetJson<Dataset[] | { items: Dataset[] }>(path);
  return Array.isArray(data) ? data : (data.items ?? []);
}

export function getDataset(id: string, version?: number) {
  const params = version != null ? `?version=${version}` : '';
  return datasetJson<DatasetDetail>(`/datasets/${seg(id)}${params}`);
}

export function listDatasetVersions(id: string) {
  return datasetJson<DatasetVersion[]>(`/datasets/${seg(id)}/versions`);
}

export function listDatasetChunks(id: string, gatePassedOnly = false) {
  return datasetJson<ChunkRecord[]>(
    `/datasets/${seg(id)}/chunks?gate_passed_only=${gatePassedOnly}`,
  );
}

export function getPipelineStats(id: string, init?: RequestInit) {
  return datasetJson<PipelineStats>(`/datasets/${seg(id)}/pipeline-stats`, init);
}

export function listDatasetTasks(id: string) {
  return datasetJson<Task[]>(`/datasets/${seg(id)}/tasks`);
}

export function createDataset(payload: DatasetCreateRequest) {
  return datasetJson<DatasetCreateResponse>('/datasets/', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function updateDataset(id: string, payload: DatasetUpdateRequest) {
  return datasetJson<DatasetUpdateResponse>(`/datasets/${seg(id)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

/** Quality revalidation state of one version (accordion in the set card). */
export function getRevalidationState(id: string, version: number) {
  return datasetJson<RevalidationState>(`/datasets/${seg(id)}/versions/${seg(version)}/revalidation`);
}

/** Start a new revalidation run (validator only, no refine) over a scope. */
export function startRevalidation(
  id: string,
  version: number,
  scope: RevalidationScope,
  validationModelId?: string,
) {
  return datasetJson<RevalidationState>(`/datasets/${seg(id)}/versions/${seg(version)}/revalidation`, {
    method: 'POST',
    body: JSON.stringify({ scope, ...(validationModelId ? { validation_model_id: validationModelId } : {}) }),
  });
}

/** Per-question verdicts of one run, before and after (comparison export). */
export function getRevalidationRunItems(id: string, version: number, runId: string) {
  return datasetJson<RevalidationRunItems>(
    `/datasets/${seg(id)}/versions/${seg(version)}/revalidation/runs/${seg(runId)}/items`,
  );
}

/** Re-run only the questions the last run failed on for technical reasons. */
export function retryRevalidation(id: string, version: number) {
  return datasetJson<RevalidationState>(
    `/datasets/${seg(id)}/versions/${seg(version)}/revalidation/retry`,
    { method: 'POST' },
  );
}

/**
 * Edit questions inside an existing version. Unlike `updateDataset`, this does
 * not create a version — manual edits belong to the version being viewed.
 */
export function patchDatasetQuestions(
  id: string,
  version: number,
  questions: QuestionPatchRequest[],
) {
  return datasetJson<QuestionsPatchResponse>(
    `/datasets/${seg(id)}/versions/${seg(version)}/questions`,
    {
      method: 'PATCH',
      body: JSON.stringify({ questions }),
    },
  );
}

export function patchDatasetMetadata(id: string, metadata: Record<string, unknown>) {
  return datasetJson<{ dataset_id: string; version: number; message: string }>(
    `/datasets/${seg(id)}/metadata`,
    {
      method: 'PATCH',
      body: JSON.stringify(metadata),
    },
  );
}

/** Upload the raw document so the worker can chunk it asynchronously (GridFS). */
export async function uploadDatasetDocument(id: string, file: File) {
  const formData = new FormData();
  formData.append('file', file, file.name);
  const response = await datasetFetch(`/datasets/${seg(id)}/document`, {
    method: 'POST',
    body: formData,
  });
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new HttpError(response.status, parseErrorBody(response.status, body), body);
  }
  return response.json() as Promise<{ dataset_id: string; file_id: string; filename: string; size: number }>;
}

export function deleteDataset(id: string) {
  return datasetJson<{ message: string }>(`/datasets/${seg(id)}`, {
    method: 'DELETE',
  });
}

export function listQueues() {
  return datasetJson<Queue[]>('/queues/');
}

export function createQueue(payload: QueueCreateRequest) {
  return datasetJson<{ queue_name?: string; message?: string }>('/queues/', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function deleteQueue(name: string) {
  return datasetJson<unknown>(`/queues/${seg(name)}`, { method: 'DELETE' });
}

/**
 * @param brief drop `chunk_text` and `result` — the large fields, needed only
 *   when a single task is opened. Polling lists should always pass it.
 */
export interface QueueTaskPage {
  skip?: number;
  sortBy?: string;
  sortDir?: 'asc' | 'desc';
}

/**
 * One page of a queue's tasks. The total to page against comes from
 * `listQueues` (`task_count` and the per-status counts), which the server
 * already computes with an aggregation.
 */
export function listQueueTasks(
  name: string,
  status?: string,
  limit?: number,
  brief = false,
  page: QueueTaskPage = {},
) {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (limit != null) params.set('limit', String(limit));
  if (brief) params.set('brief', '1');
  if (page.skip) params.set('skip', String(page.skip));
  if (page.sortBy) params.set('sort_by', page.sortBy);
  if (page.sortDir) params.set('sort_dir', page.sortDir);
  const qs = params.toString();
  return datasetJson<Task[]>(`/queues/${seg(name)}/tasks/${qs ? `?${qs}` : ''}`);
}

export function getTask(taskId: string) {
  return datasetJson<Task>(`/tasks/${seg(taskId)}`);
}

export function addQueueTasks(name: string, tasks: TaskCreateRequest[]) {
  return datasetJson<QueueTasksAddResponse>(`/queues/${seg(name)}/tasks/`, {
    method: 'POST',
    body: JSON.stringify(tasks),
  });
}

export function cancelTask(taskId: string) {
  return datasetJson<{ task_id: string; status: string; message: string }>(
    `/tasks/${seg(taskId)}/cancel`,
    { method: 'PATCH' },
  );
}

export function retryFailedTasks(name: string) {
  return datasetJson<{ tasks_retried: number; datasets_reopened: number; message: string }>(
    `/queues/${seg(name)}/retry-failed`,
    { method: 'POST' },
  );
}

export function retryAllFailedTasks() {
  return datasetJson<{ tasks_retried: number; datasets_reopened: number; message: string }>(
    '/tasks/retry-failed',
    { method: 'POST' },
  );
}

export function login(username: string, password: string) {
  return datasetJson<LoginResponse>(
    '/auth/login',
    {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    },
  );
}
