import { useTranslation } from 'react-i18next';
import { versionOptionLabel } from '@/shared/lib/versionLabel';
import type { DatasetVersion } from '@/shared/types/documents';

export function VersionSelect({
  versions,
  value,
  onChange,
}: {
  versions: DatasetVersion[];
  value: number | null;
  onChange: (version: number) => void;
}) {
  const { t } = useTranslation('results');
  if (!versions.length) return null;
  return (
    <label className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-slate-600">{t('selectVersion')}</span>
      <select
        className="input-field w-auto max-w-full"
        value={value ?? ''}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
      >
        {versions.map((v) => (
          <option key={v.version} value={v.version}>
            {versionOptionLabel(v, t)}
          </option>
        ))}
      </select>
    </label>
  );
}
