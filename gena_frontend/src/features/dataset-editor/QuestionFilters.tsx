import { useTranslation } from 'react-i18next';
import { levelLabel } from '@/shared/lib/labels';
import {
  filtersEqual,
  isEmptyFilter,
  type FilterKey,
  type QuestionFilterState,
} from '@/shared/lib/questionFilters';
import { LEVELS } from '@/shared/lib/questionStats';
import { MultiSelect, type MultiSelectOption } from '@/shared/ui/MultiSelect';

export function QuestionFilters({
  pending,
  applied,
  chunkOptions,
  onPendingChange,
  onApply,
  onReset,
}: {
  pending: QuestionFilterState;
  applied: QuestionFilterState;
  chunkOptions: string[];
  onPendingChange: (next: QuestionFilterState) => void;
  onApply: () => void;
  onReset: () => void;
}) {
  const { t } = useTranslation(['results', 'common', 'create']);
  const allLabel = t('results:filterAll');
  const countLabel = (n: number) => t('results:selectedCount', { count: n });

  const set = (key: FilterKey, values: Set<string>) =>
    onPendingChange({ ...pending, [key]: values } as QuestionFilterState);

  const levels: MultiSelectOption[] = LEVELS.map((lv) => ({ value: lv, label: levelLabel(lv, t) }));
  const dimensions: Array<{ key: FilterKey; label: string; options: MultiSelectOption[] }> = [
    {
      key: 'chunks',
      // The control is already titled "Фрагмент": bare numbers keep the list narrow.
      label: t('results:filterChunk'),
      options: chunkOptions.map((id) => ({ value: id, label: id })),
    },
    {
      key: 'quality',
      label: t('results:filterQuality'),
      options: [
        { value: 'passed', label: t('results:passed') },
        { value: 'failed', label: t('results:failed') },
        { value: 'revalidation_required', label: t('results:revalidationRequired') },
      ],
    },
    {
      key: 'types',
      label: t('results:filterType'),
      options: [
        { value: 'one', label: t('results:typeShortOne') },
        { value: 'multi', label: t('results:typeShortMulti') },
        { value: 'open', label: t('results:typeShortOpen') },
      ],
    },
    { key: 'sensitivity', label: t('results:sensitivity'), options: levels },
    { key: 'difficulty', label: t('results:difficulty'), options: levels },
    {
      key: 'editing',
      label: t('results:filterEditing'),
      options: [
        { value: 'user_edited', label: t('results:editStatusUser') },
        { value: 'paraphrased', label: t('results:editStatusParaphrased') },
        { value: 'unsaved', label: t('results:filterUnsaved') },
        { value: 'excluded', label: t('results:filterExcluded') },
      ],
    },
  ];

  const canApply = !filtersEqual(pending, applied);
  const canReset = !isEmptyFilter(applied);

  // Selects sit in a grid whose cell width depends only on the container, so
  // nothing jumps when a summary changes from "Все" to "3 выбрано". The buttons
  // are a separate column: a dropdown opens downwards and never grows past its
  // own select, so it cannot cover them. Reset is always rendered (disabled)
  // for the same reason — a column that appears and disappears reflows the grid.
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
      <div className="grid flex-1 grid-cols-[repeat(auto-fill,minmax(18rem,1fr))] gap-2">
        {dimensions.map((dim) => (
          <MultiSelect
            key={dim.key}
            label={dim.label}
            options={dim.options}
            value={pending[dim.key] as Set<string>}
            onChange={(next) => set(dim.key, next)}
            allLabel={allLabel}
            countLabel={countLabel}
          />
        ))}
      </div>
      <div className="flex flex-none gap-2">
        <button type="button" className="btn-primary py-1.5" disabled={!canApply} onClick={onApply}>
          {t('results:applyFilters')}
        </button>
        <button type="button" className="btn-secondary py-1.5" disabled={!canReset} onClick={onReset}>
          {t('results:resetFilters')}
        </button>
      </div>
    </div>
  );
}
