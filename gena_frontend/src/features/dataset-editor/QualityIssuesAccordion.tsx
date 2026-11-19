import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  failedSubcriteria,
  qualityAccordionMode,
  type Importance,
} from '@/shared/lib/validationCriteria';
import type { Question } from '@/shared/types/documents';

const DOT: Record<Importance, string> = {
  critical: 'bg-red-500',
  standard: 'bg-amber-400',
  auxiliary: 'bg-slate-400',
};

const IMPORTANCE_KEY: Record<Importance, string> = {
  critical: 'results:importanceCritical',
  standard: 'results:importanceStandard',
  auxiliary: 'results:importanceAuxiliary',
};

/**
 * Collapsed by default. Red "why did it fail" for failed questions, grey
 * "things to note" for passed ones with zero-scored checks, nothing for a
 * clean pass. Scores themselves are never shown — only the failed checks,
 * grouped by criterion, with the model's justification.
 */
export function QualityIssuesAccordion({ question }: { question: Question }) {
  const { t } = useTranslation(['results', 'common']);
  const groups = useMemo(() => failedSubcriteria(question, t), [question, t]);
  const mode = qualityAccordionMode(question, groups);
  if (mode === 'hidden') return null;
  const failed = mode === 'failed';

  return (
    <details
      className={`rounded-lg border text-sm ${
        failed ? 'border-red-200 bg-red-50/40' : 'border-slate-200 bg-slate-50'
      }`}
    >
      <summary
        className={`cursor-pointer px-3 py-2 font-medium ${failed ? 'text-red-700' : 'text-slate-600'}`}
      >
        {failed ? t('results:whyFailed') : t('results:thingsToNote')}
      </summary>
      <div className={`space-y-3 border-t px-3 py-2 ${failed ? 'border-red-100' : 'border-slate-200'}`}>
        {groups.length === 0 ? (
          <p className="text-slate-500">{t('results:noQualityDetails')}</p>
        ) : (
          groups.map((g) => (
            <div key={g.blockKey}>
              <div className="font-semibold text-slate-800">{g.blockLabel}</div>
              <ul className="mt-1 space-y-1.5">
                {g.items.map((item) => (
                  <li key={item.id} className="flex gap-2">
                    <span
                      aria-hidden
                      title={t(IMPORTANCE_KEY[item.importance])}
                      className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${DOT[item.importance]}`}
                    />
                    <div>
                      <div className="font-medium text-slate-700">{item.title}</div>
                      {item.justification && (
                        <div className="text-xs text-slate-600">{item.justification}</div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </div>
    </details>
  );
}
