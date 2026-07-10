import { normOptions } from './optionsFormat';
import type { Question } from '../types/documents';

export interface QuestionDiff {
  qid: string;
  diffs: Record<string, [string, string]>;
  meta: { v1Idx: number | null; v2Idx: number | null };
}

function normQuestionForDiff(q: Question) {
  return {
    question_type: q.question_type ?? '',
    task: q.task ?? '',
    options: normOptions(q.options),
    correct_answer: String(q.correct_answer ?? ''),
    provocativeness: String(q.provocativeness ?? ''),
    difficulty: String(q.difficulty ?? ''),
    validation_score: String(q.validation_score ?? ''),
    validation_passed: String(q.validation_passed ?? ''),
  };
}

function compareQuestions(q1: ReturnType<typeof normQuestionForDiff>, q2: ReturnType<typeof normQuestionForDiff>) {
  const diffs: Record<string, [string, string]> = {};
  const keys = ['question_type', 'task', 'options', 'correct_answer', 'provocativeness', 'difficulty'] as const;
  for (const k of keys) {
    if ((q1[k] || '') !== (q2[k] || '')) {
      diffs[k] = [q1[k] || '', q2[k] || ''];
    }
  }
  return diffs;
}

export function diffVersions(ds1: { questions?: Question[] }, ds2: { questions?: Question[] }): QuestionDiff[] {
  const q1 = ds1.questions ?? [];
  const q2 = ds2.questions ?? [];
  const hasIds1 = q1.some((q) => q.question_id);
  const hasIds2 = q2.some((q) => q.question_id);
  const diffs: QuestionDiff[] = [];

  if (hasIds1 && hasIds2) {
    const map1 = Object.fromEntries(
      q1.map((q, i) => [String(q.question_id), i]).filter(([id]) => id && id !== 'undefined'),
    );
    const map2 = Object.fromEntries(
      q2.map((q, i) => [String(q.question_id), i]).filter(([id]) => id && id !== 'undefined'),
    );
    const allIds = [...new Set([...Object.keys(map1), ...Object.keys(map2)])].sort();
    for (const qid of allIds) {
      const i = map1[qid] as number | undefined;
      const j = map2[qid] as number | undefined;
      const q1n = i != null ? normQuestionForDiff(q1[i]) : normQuestionForDiff({});
      const q2n = j != null ? normQuestionForDiff(q2[j]) : normQuestionForDiff({});
      const d = compareQuestions(q1n, q2n);
      if (Object.keys(d).length) {
        diffs.push({ qid, diffs: d, meta: { v1Idx: i ?? null, v2Idx: j ?? null } });
      }
    }
  } else {
    const n = Math.max(q1.length, q2.length);
    for (let idx = 0; idx < n; idx++) {
      const a = q1[idx] ?? {};
      const b = q2[idx] ?? {};
      const d = compareQuestions(normQuestionForDiff(a), normQuestionForDiff(b));
      if (Object.keys(d).length) {
        diffs.push({ qid: `idx:${idx}`, diffs: d, meta: { v1Idx: idx < q1.length ? idx : null, v2Idx: idx < q2.length ? idx : null } });
      }
    }
  }
  return diffs;
}
