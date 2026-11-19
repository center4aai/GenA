import { useTranslation } from 'react-i18next';

/**
 * "Включить в выгрузку" switch. Saves immediately through its own mutation,
 * so it never takes part in the card's Save / Cancel cycle.
 */
export function ExportToggle({
  included,
  pending = false,
  disabled = false,
  onChange,
}: {
  included: boolean;
  pending?: boolean;
  disabled?: boolean;
  onChange: (included: boolean) => void;
}) {
  const { t } = useTranslation('results');
  return (
    <span className="inline-flex items-center gap-2 text-sm font-medium text-slate-700">
      <span>{t('includeInExport')}</span>
      <button
        type="button"
        role="switch"
        aria-checked={included}
        aria-label={t('includeInExport')}
        disabled={disabled || pending}
        onClick={() => onChange(!included)}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gena-primary disabled:cursor-not-allowed disabled:opacity-50 ${
          included ? 'bg-emerald-500' : 'bg-slate-300'
        }`}
      >
        <span
          aria-hidden
          className={`inline-block h-5 w-5 rounded-full bg-white shadow transition-transform ${
            included ? 'translate-x-5' : 'translate-x-0.5'
          }`}
        />
      </button>
    </span>
  );
}
