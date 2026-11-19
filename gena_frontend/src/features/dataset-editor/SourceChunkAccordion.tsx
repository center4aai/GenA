import { useTranslation } from 'react-i18next';

/** The chunk the question was generated from, at the bottom of the card. */
export function SourceChunkAccordion({ text }: { text?: string | null }) {
  const { t } = useTranslation('results');
  if (!text) return null;
  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50">
      <summary className="cursor-pointer px-3 py-2 text-sm text-slate-600">{t('sourceText')}</summary>
      <div className="border-t border-slate-200 px-3 py-2 text-xs">
        <div className="flex justify-end">
          <button
            type="button"
            className="text-xs text-gena-primary hover:underline"
            onClick={() => void navigator.clipboard.writeText(text)}
          >
            {t('copy')}
          </button>
        </div>
        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap">{text}</pre>
      </div>
    </details>
  );
}
