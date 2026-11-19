import type { LlmModel, ModelHealth } from '@/shared/types/documents';

/** Model id → available, from /models/health/. A missing id means "status unknown". */
export type ModelHealthMap = Record<string, boolean>;

/** Health list → map; `undefined` (not loaded yet or the request failed) → empty map. */
export function toHealthMap(health: ModelHealth[] | undefined): ModelHealthMap {
  return Object.fromEntries((health ?? []).map((h) => [h.id, h.available]));
}

/**
 * Models a user may pick: everything except those the health probe reports down.
 * Unknown status (no health entry, health not loaded or failed) keeps the model,
 * so a broken health endpoint never hides the whole list.
 */
export function selectableModels(models: LlmModel[], health: ModelHealthMap): LlmModel[] {
  return models.filter((m) => health[m.id] !== false);
}

/** First preferred id that is still selectable, else the first selectable model, else ''. */
export function pickModelId(models: LlmModel[], ...preferred: (string | null | undefined)[]): string {
  const ids = new Set(models.map((m) => m.id));
  return preferred.find((id): id is string => !!id && ids.has(id)) ?? models[0]?.id ?? '';
}
