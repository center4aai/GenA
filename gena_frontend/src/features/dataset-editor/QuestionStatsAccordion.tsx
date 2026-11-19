import { useTranslation } from 'react-i18next';
import { levelLabel } from '@/shared/lib/labels';
import { LEVELS, type QuestionStats } from '@/shared/lib/questionStats';
import { CollapsibleSection } from '@/shared/ui/CollapsibleSection';

type StatRow = [string, number];
type LevelRecord = Record<(typeof LEVELS)[number], number>;

function StatColumn({ title, rows }: { title: string; rows: StatRow[] }) {
  return (
    <div>
      <div className="font-semibold text-slate-800">{title}</div>
      <dl className="mt-1 grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5 text-slate-700">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt>{label}</dt>
            <dd className="text-right tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Per-version counts by type, sensitivity and difficulty; collapsed by default. */
export function QuestionStatsAccordion({ stats }: { stats: QuestionStats }) {
  const { t } = useTranslation(['results', 'create']);
  const levels = (record: LevelRecord): StatRow[] => LEVELS.map((l) => [levelLabel(l, t), record[l]]);
  return (
    <CollapsibleSection title={t('results:statsTitle')}>
      <div className="grid gap-6 text-sm sm:grid-cols-3">
        <StatColumn
          title={t('results:statsTypes')}
          rows={[
            [t('create:typeOne'), stats.types.one],
            [t('create:typeMulti'), stats.types.multi],
            [t('create:typeOpen'), stats.types.open],
          ]}
        />
        <StatColumn title={t('results:sensitivity')} rows={levels(stats.sensitivity)} />
        <StatColumn title={t('results:difficulty')} rows={levels(stats.difficulty)} />
      </div>
    </CollapsibleSection>
  );
}
