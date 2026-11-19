import { useTranslation } from 'react-i18next';
import {
  addOption,
  canAddOption,
  canRemoveOption,
  isCorrectDisabled,
  removeOption,
  toggleCorrect,
  type ChoiceMode,
} from '@/shared/lib/optionsEdit';
import { draftValidation, type QuestionDraft } from '@/shared/lib/questionDraft';

/**
 * Numbered option fields with a "correct" checkbox per row. All rules
 * (4–9 options, radio behaviour for single choice, multi cap, minimum two
 * correct, exactly one for single) live in shared/lib/optionsEdit and
 * questionDraft — this component only renders them.
 */
export function OptionsEditor({
  draft,
  rawOptions,
  readOnly,
  onChange,
}: {
  draft: QuestionDraft;
  /** The stored options string, shown as-is when it cannot be edited. */
  rawOptions?: string;
  readOnly: boolean;
  onChange: (patch: Partial<QuestionDraft>) => void;
}) {
  const { t } = useTranslation('results');
  const mode = draft.question_type as ChoiceMode;
  const texts = draft.optionTexts;
  const count = texts.length;
  const validation = draftValidation(draft);

  if (draft.optionsLocked) {
    return (
      <div className="space-y-1">
        <div className="text-sm font-medium text-slate-700">{t('answerOptions')}</div>
        <pre className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-sans text-sm">{rawOptions}</pre>
        <p className="text-xs text-amber-700">{t('optionsLocked')}</p>
      </div>
    );
  }

  const showRemove = !readOnly && canRemoveOption(count);

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-x-3 gap-y-2">
        <div className="col-span-3 text-sm font-medium text-slate-700">{t('answerOptions')}</div>
        <div className="text-sm font-medium text-slate-700">{t('correctOptions')}</div>
        {texts.map((text, i) => {
          const n = i + 1;
          const marked = draft.correct.includes(n);
          return (
            <div key={n} className="contents">
              <span className="w-5 text-right text-sm text-slate-500">{n}</span>
              <textarea
                className="input-field max-h-24 resize-none overflow-auto py-1.5"
                rows={1}
                value={text}
                disabled={readOnly}
                aria-label={t('optionN', { n })}
                onChange={(e) => onChange({ optionTexts: texts.map((v, j) => (j === i ? e.target.value : v)) })}
              />
              {showRemove ? (
                <button
                  type="button"
                  className="rounded px-1.5 text-lg leading-none text-slate-400 hover:bg-slate-100 hover:text-red-600"
                  aria-label={t('removeOption', { n })}
                  onClick={() => {
                    const next = removeOption(texts, draft.correct, n);
                    onChange({ optionTexts: next.texts, correct: next.correct, touchedCorrect: true });
                  }}
                >
                  ×
                </button>
              ) : (
                <span />
              )}
              <label className="flex items-center justify-center gap-1 text-sm text-slate-500">
                <span className="tabular-nums">{n}</span>
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={marked}
                  disabled={readOnly || isCorrectDisabled(draft.correct, n, mode, count)}
                  aria-label={t('correctOptionN', { n })}
                  onChange={() =>
                    onChange({ correct: toggleCorrect(draft.correct, n, mode, count), touchedCorrect: true })
                  }
                />
              </label>
            </div>
          );
        })}
      </div>
      {!readOnly && canAddOption(count) && (
        <button
          type="button"
          className="btn-secondary py-1 text-xs"
          onClick={() => onChange({ optionTexts: addOption(texts) })}
        >
          + {t('addOption')}
        </button>
      )}
      {/* Structural hints first; "mark an answer" waits until there are enough options to mark. */}
      {!readOnly && validation.tooFewOptions && <p className="text-xs text-amber-700">{t('minOptionsHint')}</p>}
      {!readOnly && validation.emptyOption && <p className="text-xs text-amber-700">{t('emptyOptionHint')}</p>}
      {!readOnly && validation.tooManyCorrect && <p className="text-xs text-amber-700">{t('singleCorrectHint')}</p>}
      {!readOnly && !validation.tooFewOptions && validation.tooFewCorrect && (
        <p className="text-xs text-amber-700">{mode === 'multi' ? t('minTwoCorrect') : t('selectCorrectOption')}</p>
      )}
    </div>
  );
}
