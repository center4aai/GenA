import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addQueueTasks,
  deleteQueue,
  getDataset,
  listQueueTasks,
  patchDatasetQuestions,
  retryFailedTasks,
} from './dataset';

const QUEUE = 'queue_Тест #1/2 — 3 сент.';
const ENCODED = encodeURIComponent(QUEUE);

function jsonResponse(body: unknown = []) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('dataset API path encoding', () => {
  const fetchMock = vi.fn(async () => jsonResponse());

  beforeEach(() => {
    fetchMock.mockClear();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function requestedUrl(): string {
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    return url;
  }

  it('encodes the queue name in listQueueTasks and keeps the query string readable', async () => {
    await listQueueTasks(QUEUE, 'failed', 50, true, { skip: 100, sortBy: 'updated_at' });
    expect(requestedUrl()).toBe(
      `/api/dataset/queues/${ENCODED}/tasks/?status=failed&limit=50&brief=1&skip=100&sort_by=updated_at`,
    );
    expect(ENCODED).not.toMatch(/[#/]/);
  });

  it('encodes the queue name in deleteQueue, addQueueTasks and retryFailedTasks', async () => {
    await deleteQueue(QUEUE);
    expect(requestedUrl()).toBe(`/api/dataset/queues/${ENCODED}`);

    fetchMock.mockClear();
    await addQueueTasks(QUEUE, []);
    expect(requestedUrl()).toBe(`/api/dataset/queues/${ENCODED}/tasks/`);

    fetchMock.mockClear();
    await retryFailedTasks(QUEUE);
    expect(requestedUrl()).toBe(`/api/dataset/queues/${ENCODED}/retry-failed`);
  });

  it('sends the include-in-export toggle as a one-field question patch', async () => {
    await patchDatasetQuestions('ds1', 2, [{ question_id: 'q1', excluded_from_export: true }]);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toEqual({
      questions: [{ question_id: 'q1', excluded_from_export: true }],
    });
  });

  it('encodes dataset ids and version segments', async () => {
    await getDataset('a/b#c', 3);
    expect(requestedUrl()).toBe('/api/dataset/datasets/a%2Fb%23c?version=3');

    fetchMock.mockClear();
    await patchDatasetQuestions('a/b#c', 2, []);
    expect(requestedUrl()).toBe('/api/dataset/datasets/a%2Fb%23c/versions/2/questions');
  });
});
