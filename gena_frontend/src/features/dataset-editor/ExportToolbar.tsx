import { useTranslation } from 'react-i18next';
import { StatusBanner } from '@/shared/ui/StatusBanner';

export interface ExportToolbarProps {
  excludedCount: number;
  /** Filter the list down to the excluded questions (the hint above the buttons is clickable). */
  onShowExcluded: () => void;
  shuffle: boolean;
  onShuffleChange: (value: boolean) => void;
  onExcel: () => void;
  onGift: () => void;
  onPrint: () => void;
  onExtended: () => void;
  message: string | null;
}

/** Download / print actions of the dataset card, with the extended export set apart. */
export function ExportToolbar({
  excludedCount,
  onShowExcluded,
  shuffle,
  onShuffleChange,
  onExcel,
  onGift,
  onPrint,
  onExtended,
  message,
}: ExportToolbarProps) {
  const { t } = useTranslation(['results', 'advanced']);
  return (
    <div className="flex w-full flex-col gap-3 lg:w-72">
      {excludedCount > 0 && (
        <button
          type="button"
          className="self-start rounded-full bg-slate-100 px-2.5 py-0.5 text-left text-xs font-medium text-slate-700 hover:bg-slate-200"
          title={t('results:excludedFilterHint')}
          onClick={onShowExcluded}
        >
          {t('results:excludedCount', { count: excludedCount })} →
        </button>
      )}
      {message && <StatusBanner tone="error">{message}</StatusBanner>}
      <button type="button" className="btn-primary" onClick={onExcel}>
        {t('results:downloadExcel')}
      </button>
      <button type="button" className="btn-secondary" onClick={onGift}>
        {t('advanced:exportGift')}
      </button>
      <button type="button" className="btn-secondary" onClick={onPrint}>
        {t('advanced:exportPdfPrint')}
      </button>
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" checked={shuffle} onChange={(e) => onShuffleChange(e.target.checked)} />
        {t('results:shuffleOnExport')}
      </label>
      <div className="border-t border-slate-200 pt-3">
        <button type="button" className="btn-primary w-full py-1.5 text-xs" onClick={onExtended}>
          {t('results:downloadExtended')}
        </button>
        <p className="mt-1 text-xs text-slate-500">{t('results:downloadExtendedHint')}</p>
      </div>
    </div>
  );
}
