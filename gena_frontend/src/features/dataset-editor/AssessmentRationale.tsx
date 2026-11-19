import { useTranslation } from 'react-i18next';
import { levelLabel } from '@/shared/lib/labels';
import { originalDifficultyValue, originalSensitivityValue } from '@/shared/lib/questionFields';
import type { Question } from '@/shared/types/documents';

/**
 * The model's original sensitivity / difficulty reasoning, collapsed under the
 * selects. Levels shown are the model's (`*_original`, kept by the server on
 * the first manual change), not the user's current picks.
 */
export function AssessmentRationale({ question }: { question: Question }) {
  const { t } = useTranslation(['results', 'common']);
  const sens = question.sensitivity_explanation?.trim();
  const diff = question.difficulty_explanation?.trim();
  if (!sens && !diff) return null;

  const level = (value: string) => (value ? levelLabel(value, t) : t('common:none'));

  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50 text-sm">
      <summary className="cursor-pointer px-3 py-2 text-slate-600">{t('results:rationaleTitle')}</summary>
      <div className="space-y-2 border-t border-slate-200 px-3 py-2">
        {sens && (
          <div>
            <div className="font-medium text-slate-800">
              {t('results:sensitivityOriginal', { level: level(originalSensitivityValue(question)) })}
            </div>
            <p className="text-xs text-slate-600">{sens}</p>
          </div>
        )}
        {diff && (
          <div>
            <div className="font-medium text-slate-800">
              {t('results:difficultyOriginal', { level: level(originalDifficultyValue(question)) })}
            </div>
            <p className="text-xs text-slate-600">{diff}</p>
          </div>
        )}
      </div>
    </details>
  );
}
