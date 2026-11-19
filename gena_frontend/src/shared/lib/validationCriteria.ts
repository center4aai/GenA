import { isValidationPassed } from '@/shared/lib/progress';
import { isValidationCurrent } from '@/shared/lib/revalidation';
import { normalizeQuestionType, type QuestionKind } from '@/shared/lib/questionFields';
import {
  VALIDATION_BLOCK_META_KEYS,
  blockLabel,
  parseValidationDetails,
  parseValidationJustifications,
  type Translate,
} from '@/shared/lib/validation';
import type { Question } from '@/shared/types/documents';

/**
 * Static description of the validator's sub-criteria, in the order their
 * scores appear in `validation_details[block]`. Weights and critical
 * (score-zeroing) flags mirror agent_api/agent/nodes/llm_validator/validator.py
 * (WEIGHTS / MULTIPLIER_INDICES); titles are i18n keys seeded from the prompt
 * files and meant to be replaced by the product team's wording.
 */
export type Importance = 'critical' | 'standard' | 'auxiliary';

export interface SubCriterion {
  id: string;
  titleKey: string;
  weight: 0.5 | 1;
  critical: boolean;
}

export interface CriterionBlock {
  block: string;
  sub: SubCriterion[];
}

function sub(id: string, title: string, weight: 0.5 | 1 = 1, critical = false): SubCriterion {
  return { id, titleKey: `results:valSub${title}`, weight, critical };
}

const C1_QUESTION: CriterionBlock = {
  block: 'c1_question',
  sub: [
    sub('question_bravity', 'QuestionBravity', 0.5),
    sub('question_basis', 'QuestionBasis', 1, true),
    sub('question_clarity', 'QuestionClarity', 1, true),
    sub('question_not_binary', 'QuestionNotBinary'),
    sub('question_context', 'QuestionContext', 1, true),
  ],
};

const C2_OUTPUTS: CriterionBlock = {
  block: 'c2_outputs',
  sub: [
    sub('outputs_bravity', 'OutputsBravity', 0.5),
    sub('outputs_question_duplication', 'OutputsQuestionDuplication', 1, true),
    sub('outputs_basis', 'OutputsBasis', 1, true),
    sub('outputs_clarity', 'OutputsClarity'),
    sub('outputs_unambiguity', 'OutputsUnambiguity'),
    sub('outputs_completeness', 'OutputsCompleteness'),
  ],
};

function c2Options(kind: 'one' | 'multi'): CriterionBlock {
  return {
    block: 'c2_options',
    sub: [
      sub('options_count', 'OptionsCount'),
      sub('options_right_count', kind === 'one' ? 'OptionsRightCountOne' : 'OptionsRightCountMulti', 0.5),
      sub('options_bravity', 'OptionsBravity', 0.5),
      sub('options_question_duplication', 'OptionsQuestionDuplication', 1, true),
      sub('options_duplication', 'OptionsDuplication', 1, true),
      sub('options_right_unambiguity', 'OptionsRightUnambiguity'),
      sub('options_right_completeness', 'OptionsRightCompleteness'),
      sub('options_right_basis', 'OptionsRightBasis', 1, true),
      sub('options_wrong_believability', 'OptionsWrongBelievability'),
    ],
  };
}

const C3_OUTPUTS: CriterionBlock = {
  block: 'c3_outputs',
  sub: [sub('outputs_include', 'OutputsInclude', 1, true), sub('outputs_exclude', 'OutputsExclude', 1, true)],
};

function c4Logic(kind: QuestionKind): CriterionBlock {
  return {
    block: 'c4_logic',
    sub: [
      sub('logic_unambiguity', 'LogicUnambiguity'),
      sub('logic_focus', 'LogicFocus'),
      sub('logic_contradiction', 'LogicContradiction'),
      sub('logic_link', kind === 'open' ? 'LogicLinkOpen' : 'LogicLinkChoice'),
    ],
  };
}

const C5_PHRASE: CriterionBlock = {
  block: 'c5_phrase',
  sub: [sub('phrase_grammar', 'PhraseGrammar'), sub('phrase_wording', 'PhraseWording')],
};

export const CRITERIA: Record<QuestionKind, CriterionBlock[]> = {
  open: [C1_QUESTION, C2_OUTPUTS, c4Logic('open'), C5_PHRASE],
  one: [C1_QUESTION, c2Options('one'), C3_OUTPUTS, c4Logic('one'), C5_PHRASE],
  multi: [C1_QUESTION, c2Options('multi'), C3_OUTPUTS, c4Logic('multi'), C5_PHRASE],
};

/** Red for score-zeroing checks, yellow for full-weight ones, grey for half-weight. */
export function importanceOf(s: SubCriterion): Importance {
  if (s.critical) return 'critical';
  return s.weight < 1 ? 'auxiliary' : 'standard';
}

export interface FailedSubcriterion {
  id: string;
  title: string;
  importance: Importance;
  justification: string;
}

export interface FailedCriterionGroup {
  blockKey: string;
  blockLabel: string;
  items: FailedSubcriterion[];
}

function translate(t: Translate | undefined, key: string): string {
  return t ? t(key) : key;
}

/** The validator's justifications arrive with markdown emphasis; the card renders plain text. */
export function plainJustification(text: unknown): string {
  return String(text ?? '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .trim();
}

/**
 * Sub-criteria the validator scored 0, grouped by criterion in validator
 * order, each with the model's justification. Passed checks are omitted;
 * unknown blocks or indices fall back to a numbered label so nothing is lost.
 */
export function failedSubcriteria(q: Question, t?: Translate): FailedCriterionGroup[] {
  const details = parseValidationDetails(q.validation_details);
  const justifications = parseValidationJustifications(q.validation_justifications);
  const table = CRITERIA[normalizeQuestionType(q.question_type) ?? 'open'];
  const known = table.map((b) => b.block);
  const keys = [
    ...known.filter((k) => k in details),
    ...Object.keys(details).filter((k) => !VALIDATION_BLOCK_META_KEYS.has(k) && !known.includes(k)),
  ];
  const groups: FailedCriterionGroup[] = [];
  for (const key of keys) {
    const scores = details[key];
    if (!Array.isArray(scores)) continue;
    const block = table.find((b) => b.block === key);
    const items: FailedSubcriterion[] = [];
    scores.forEach((score, i) => {
      if (Number(score) !== 0) return;
      const meta = block?.sub[i];
      items.push({
        id: meta?.id ?? `${key}_${i + 1}`,
        title: meta ? translate(t, meta.titleKey) : `${blockLabel(key, t)} #${i + 1}`,
        importance: meta ? importanceOf(meta) : 'standard',
        justification: plainJustification(justifications[key]?.[i]),
      });
    });
    if (items.length) groups.push({ blockKey: key, blockLabel: blockLabel(key, t), items });
  }
  return groups;
}

export type QualityAccordionMode = 'failed' | 'note' | 'hidden';

/**
 * failed: the question did not pass — red title, always shown.
 * note:   passed, but some check scored 0 — grey title.
 * hidden: passed with nothing to report, or no verdict at all.
 */
export function qualityAccordionMode(q: Question, groups?: FailedCriterionGroup[]): QualityAccordionMode {
  // The reasoning of a stale verdict explains wording that no longer exists.
  if (!isValidationCurrent(q)) return 'hidden';
  const passed = isValidationPassed(q);
  if (passed === false) return 'failed';
  if (passed === true && (groups ?? failedSubcriteria(q)).length) return 'note';
  return 'hidden';
}
