import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { levelLabel, questionTypeLabel } from '@/shared/lib/labels';
import { qualityState } from '@/shared/lib/revalidation';
import {
  draftFromQuestion,
  draftValidation,
  isChoiceKind,
  withQuestionType,
  type LevelValue,
  type QuestionDraft,
} from '@/shared/lib/questionDraft';
import { isExcludedFromExport } from '@/shared/lib/questionEdits';
import { hasRealQuestionId, type QuestionKind } from '@/shared/lib/questionFields';
import { LEVELS } from '@/shared/lib/questionStats';
import type { Question } from '@/shared/types/documents';
import { Pill } from '@/shared/ui/Pill';
import { StatusBanner } from '@/shared/ui/StatusBanner';
import { AssessmentRationale } from './AssessmentRationale';
import { ExportToggle } from './ExportToggle';
import { OptionsEditor } from './OptionsEditor';
import { QualityIssuesAccordion } from './QualityIssuesAccordion';
import { SourceChunkAccordion } from './SourceChunkAccordion';

export interface QuestionCardProps {
  /** The question as stored on the server (the draft is compared against it). */
  question: Question;
  /** 0-based position in the version — the card number stays global under filters. */
  index: number;
  /** Staged edits; undefined when the card is clean. */
  draft?: QuestionDraft;
  readOnly: boolean;
  /** A quality revalidation runs for this version: wording is locked, export toggle is not. */
  editLocked?: boolean;
  saving: boolean;
  saved: boolean;
  saveError: string | null;
  exportPending: boolean;
  onDraftChange: (patch: Partial<QuestionDraft>) => void;
  onSave: () => void;
  onCancel: () => void;
  onToggleExport: (included: boolean) => void;
}

const KINDS: QuestionKind[] = ['one', 'multi', 'open'];

export function QuestionCard({
  question,
  index,
  draft,
  readOnly,
  editLocked = false,
  saving,
  saved,
  saveError,
  exportPending,
  onDraftChange,
  onSave,
  onCancel,
  onToggleExport,
}: QuestionCardProps) {
  const { t } = useTranslation(['results', 'common', 'create']);
  const base = useMemo(() => draftFromQuestion(question), [question]);
  const d = draft ?? base;
  const dirty = draft != null;
  const addressable = hasRealQuestionId(question);
  const editable = !readOnly && addressable && !editLocked;
  const validation = draftValidation(d);
  const quality = qualityState(question);
  const included = !isExcludedFromExport(question);

  return (
    <article className={`card space-y-3 ${included ? '' : 'border-dashed'}`}>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-medium">
          {t('results:questionN', { n: index + 1 })}
          <span className="ml-2 text-sm font-normal text-slate-500">
            {t('results:fromChunk', { n: question.chunk_id ?? t('common:none') })}
          </span>
        </h4>
        <div className="flex flex-wrap items-center gap-2">
          {question.edit_status === 'user_edited' && <Pill tone="info">{t('results:editStatusUser')}</Pill>}
          {question.edit_status === 'paraphrased' && <Pill tone="info">{t('results:editStatusParaphrased')}</Pill>}
          {quality === 'passed' && <Pill tone="success">{t('results:passed')}</Pill>}
          {quality === 'failed' && <Pill tone="danger">{t('results:failed')}</Pill>}
          {quality === 'revalidation_required' && (
            <Pill tone="warning">{t('results:revalidationRequired')}</Pill>
          )}
        </div>
      </header>

      {!readOnly && (
        <ExportToggle
          included={included}
          pending={exportPending}
          disabled={!addressable}
          onChange={onToggleExport}
        />
      )}
      {!readOnly && !addressable && <p className="text-xs text-slate-500">{t('results:noQuestionId')}</p>}

      <QualityIssuesAccordion question={question} />

      <div className="grid gap-3 sm:grid-cols-3">
        <select
          className="input-field"
          value={d.question_type}
          disabled={!editable}
          onChange={(e) => onDraftChange(withQuestionType(d, e.target.value as QuestionKind))}
        >
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {questionTypeLabel(k, t)}
            </option>
          ))}
        </select>
        <select
          className="input-field"
          value={d.provocativeness}
          disabled={!editable}
          onChange={(e) => onDraftChange({ provocativeness: e.target.value as LevelValue })}
        >
          <option value="">{t('results:sensitivityOption', { level: t('common:none') })}</option>
          {LEVELS.map((lv) => (
            <option key={lv} value={lv}>
              {t('results:sensitivityOption', { level: levelLabel(lv, t) })}
            </option>
          ))}
        </select>
        <select
          className="input-field"
          value={d.difficulty}
          disabled={!editable}
          onChange={(e) => onDraftChange({ difficulty: e.target.value as LevelValue })}
        >
          <option value="">{t('results:difficultyNone')}</option>
          {LEVELS.map((lv) => (
            <option key={lv} value={lv}>
              {t('results:difficultyOption', { level: levelLabel(lv, t) })}
            </option>
          ))}
        </select>
      </div>

      <AssessmentRationale question={question} />

      <label className="block space-y-1">
        <span className="text-sm font-medium text-slate-700">{t('results:task')}</span>
        <textarea
          className="input-field"
          rows={3}
          value={d.task}
          disabled={!editable}
          onChange={(e) => onDraftChange({ task: e.target.value })}
        />
      </label>

      {isChoiceKind(d.question_type) ? (
        <OptionsEditor
          draft={d}
          rawOptions={typeof question.options === 'string' ? question.options : undefined}
          readOnly={!editable}
          onChange={onDraftChange}
        />
      ) : (
        <label className="block space-y-1">
          <span className="text-sm font-medium text-slate-700">{t('results:correctAnswer')}</span>
          <textarea
            className="input-field"
            rows={2}
            value={d.correct_answer}
            disabled={!editable}
            onChange={(e) => onDraftChange({ correct_answer: e.target.value })}
          />
        </label>
      )}

      {editable && dirty && (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="btn-primary"
            disabled={saving || !validation.canSave}
            onClick={onSave}
          >
            {saving ? t('common:loading') : t('results:saveChanges')}
          </button>
          <button type="button" className="btn-secondary" disabled={saving} onClick={onCancel}>
            {t('common:cancel')}
          </button>
        </div>
      )}
      {saveError && <StatusBanner tone="error">{saveError}</StatusBanner>}
      {saved && !dirty && <StatusBanner tone="success">{t('results:saved')}</StatusBanner>}

      <SourceChunkAccordion text={question.source_chunk} />
    </article>
  );
}
