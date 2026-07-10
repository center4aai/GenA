import { datasetJson } from './http';
import type { ChunkRecord, Dataset, DatasetVersion, Queue, Task } from '../types/documents';

/** Backend returns a plain array from GET /datasets/ (not paginated). */
export async function listDatasets(): Promise<Dataset[]> {
  const data = await datasetJson<Dataset[] | { items: Dataset[] }>('/datasets/');
  return Array.isArray(data) ? data : (data.items ?? []);
}

export function getDataset(id: string, version?: number) {
  const params = version != null ? `?version=${version}` : '';
  return datasetJson<Dataset>(`/datasets/${id}${params}`);
}

export function listDatasetVersions(id: string) {
  return datasetJson<DatasetVersion[]>(`/datasets/${id}/versions`);
}

export function listDatasetChunks(id: string, gatePassedOnly = false) {
  return datasetJson<ChunkRecord[]>(
    `/datasets/${id}/chunks?gate_passed_only=${gatePassedOnly}`,
  );
}

export function listDatasetTasks(id: string) {
  return datasetJson<Task[]>(`/datasets/${id}/tasks`);
}

export function createDataset(payload: Partial<Dataset>) {
  return datasetJson<{ dataset_id: string }>('/datasets/', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function updateDataset(id: string, payload: { questions: unknown[]; metadata?: Record<string, unknown> }) {
  return datasetJson<{ new_version: number }>(`/datasets/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function saveDatasetChunks(id: string, chunks: unknown[]) {
  return datasetJson<unknown>(`/datasets/${id}/chunks`, {
    method: 'POST',
    body: JSON.stringify(chunks),
  });
}

export function listQueues() {
  return datasetJson<Queue[]>('/queues/');
}

export function createQueue(payload: { name: string; description?: string; priority?: number }) {
  return datasetJson<unknown>('/queues/', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function deleteQueue(name: string) {
  return datasetJson<unknown>(`/queues/${name}`, { method: 'DELETE' });
}

export function listQueueTasks(name: string, status?: string) {
  const params = status ? `?status=${status}` : '';
  return datasetJson<Task[]>(`/queues/${name}/tasks/${params}`);
}

export function addQueueTasks(name: string, tasks: unknown[]) {
  return datasetJson<{ tasks_added: number }>(`/queues/${name}/tasks/`, {
    method: 'POST',
    body: JSON.stringify(tasks),
  });
}

export function retryFailedTasks(name: string) {
  return datasetJson<{ message: string }>(`/queues/${name}/retry-failed`, {
    method: 'POST',
  });
}

export function retryAllFailedTasks() {
  return datasetJson<{ tasks_retried: number; datasets_reopened: number; message: string }>(
    '/tasks/retry-failed',
    { method: 'POST' },
  );
}

export function login(username: string, password: string) {
  return datasetJson<{ access_token?: string; token?: string; role?: string }>(
    '/auth/login',
    {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    },
  );
}
