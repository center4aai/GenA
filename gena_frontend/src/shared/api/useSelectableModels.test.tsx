import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { listModels, listModelsHealth } from './agent';
import { useSelectableModels } from './useSelectableModels';

vi.mock('./agent', () => ({ listModels: vi.fn(), listModelsHealth: vi.fn() }));

const MODELS = [
  { id: 'up', name: 'Up' },
  { id: 'down', name: 'Down' },
];

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function ids(result: { current: { models: { id: string }[] } }) {
  return result.current.models.map((m) => m.id);
}

describe('useSelectableModels', () => {
  beforeEach(() => {
    vi.mocked(listModels).mockResolvedValue(MODELS);
  });

  it('drops models the health probe reports down, keeping the full list aside', async () => {
    vi.mocked(listModelsHealth).mockResolvedValue([
      { id: 'up', name: 'Up', available: true },
      { id: 'down', name: 'Down', available: false, reason: 'not served' },
    ]);
    const { result } = renderHook(() => useSelectableModels(), { wrapper });
    await waitFor(() => expect(ids(result)).toEqual(['up']));
    expect(result.current.allModels.map((m) => m.id)).toEqual(['up', 'down']);
  });

  it('offers every model while health is still loading', async () => {
    vi.mocked(listModelsHealth).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useSelectableModels(), { wrapper });
    await waitFor(() => expect(ids(result)).toEqual(['up', 'down']));
  });

  it('offers every model when the health request fails', async () => {
    vi.mocked(listModelsHealth).mockRejectedValue(new Error('502'));
    const { result } = renderHook(() => useSelectableModels(), { wrapper });
    await waitFor(() => expect(ids(result)).toEqual(['up', 'down']));
  });
});
