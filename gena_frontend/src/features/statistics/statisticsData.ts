import { getDataset } from '@/shared/api/dataset';
import { computeDatasetProgress, isValidationPassed } from '@/shared/lib/progress';
import { mapWithConcurrency } from '@/shared/lib/async';
import type { Dataset, Question } from '@/shared/types/documents';

export interface DsStatRow {
  name: string;
  status: string;
  progress: number;
  generated: number;
  expected: number;
  validationRate: number;
  created: string;
  lastUpdated: string;
}

export interface StatisticsData {
  allQuestions: Question[];
  dsStats: DsStatRow[];
}

/** Stable query key so the page and the background prefetch share one cache entry. */
export function statisticsQueryKey(datasets: Pick<Dataset, '_id'>[]): (string)[] {
  return ['statistics-full', datasets.map((d) => d._id).join(',')];
}

/**
 * Fetch every dataset (bounded concurrency) and aggregate the analytics shown on
 * the Statistics page. Shared by the page's query and the background prefetch so
 * the two never diverge.
 */
export async function computeStatistics(datasets: Dataset[]): Promise<StatisticsData> {
  const fetched = await mapWithConcurrency(datasets, 6, (d) => getDataset(d._id).catch(() => null));

  const allQuestions: Question[] = [];
  const dsStats: DsStatRow[] = [];

  for (let i = 0; i < datasets.length; i++) {
    const d = datasets[i];
    const ds = fetched[i];
    if (!ds) continue;
    const questions = ds.questions ?? [];
    const meta = ds.metadata ?? {};

    for (const q of questions) {
      allQuestions.push({ ...q, dataset_name: ds.name, dataset_id: d._id });
    }

    const considered = questions.map(isValidationPassed).filter((p) => p !== null) as boolean[];
    const validationRate = considered.length
      ? (considered.filter(Boolean).length / considered.length) * 100
      : 0;

    const prog = computeDatasetProgress(d, ds, []);

    dsStats.push({
      name: ds.name,
      status: prog.status,
      progress: prog.progressPercent,
      generated: prog.totalQuestionsGenerated,
      expected: prog.expectedQuestions,
      validationRate,
      created: ds.created_at ?? '',
      lastUpdated: ds.updated_at ?? meta.last_updated ?? '',
    });
  }

  return { allQuestions, dsStats };
}
