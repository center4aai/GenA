import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listModels, listModelsHealth } from './agent';
import { selectableModels, toHealthMap } from '@/shared/lib/modelAvailability';

/**
 * LLM models for a picker: `/models/` minus those `/models/health/` reports down.
 * While health is loading or after its request failed every model is offered.
 * `allModels` stays complete for labels of past runs whose model is down now.
 */
export function useSelectableModels({ enabled = true }: { enabled?: boolean } = {}) {
  const modelsQuery = useQuery({ queryKey: ['models'], queryFn: listModels, enabled });
  const healthQuery = useQuery({ queryKey: ['models-health'], queryFn: listModelsHealth, enabled });
  const allModels = useMemo(() => modelsQuery.data ?? [], [modelsQuery.data]);
  const healthData = healthQuery.isError ? undefined : healthQuery.data;
  const models = useMemo(() => selectableModels(allModels, toHealthMap(healthData)), [allModels, healthData]);
  return {
    models,
    allModels,
    isLoading: modelsQuery.isLoading,
    isError: modelsQuery.isError,
    error: modelsQuery.error,
  };
}
