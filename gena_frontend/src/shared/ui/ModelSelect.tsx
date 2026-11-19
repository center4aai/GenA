import type { LlmModel } from '@/shared/types/documents';

/** LLM model picker; the caller passes only selectable models (see useSelectableModels). */
export function ModelSelect({
  models,
  value,
  onChange,
  id,
  className = 'input-field mt-1',
  disabled,
}: {
  models: LlmModel[];
  value: string;
  onChange: (id: string) => void;
  id?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <select id={id} className={className} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {models.map((m) => (
        <option key={m.id} value={m.id}>
          {m.name}
        </option>
      ))}
    </select>
  );
}

/** Display name of a model id; unknown ids (e.g. retired endpoints) as is. */
export function modelLabel(models: LlmModel[], modelId: string | null | undefined): string {
  if (!modelId) return '';
  return models.find((m) => m.id === modelId)?.name ?? modelId;
}
