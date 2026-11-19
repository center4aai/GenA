import { useTranslation } from 'react-i18next';
import type { ModelHealth } from '@/shared/types/documents';
import { StatusBanner } from '@/shared/ui/StatusBanner';

/** Models that answer the health probe; unavailable ones are not shown anywhere. */
export function availableHealth(health: ModelHealth[]): ModelHealth[] {
  return health.filter((h) => h.available);
}

/** Admin block: how many models are available now, or a warning when none is. */
export function ModelHealthBanner({ health }: { health: ModelHealth[] }) {
  const { t } = useTranslation('progress');
  const available = availableHealth(health);
  if (available.length) {
    return (
      <StatusBanner tone="success">
        <div>{t('progress:modelsAvailable', { n: available.length })}</div>
        <p className="mt-1 text-xs text-emerald-800/80">{available.map((h) => h.name).join(', ')}</p>
      </StatusBanner>
    );
  }
  if (health.length) return <StatusBanner tone="error">{t('progress:modelsUnavailable')}</StatusBanner>;
  return <p className="text-slate-500">{t('progress:noModelHealth')}</p>;
}
