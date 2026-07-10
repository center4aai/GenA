import type { Dataset, DatasetMetadata, Question, Task, TaskProgress, QueueStats } from '../types/documents';

function countByStatus(tasks: Task[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const t of tasks) {
    const status = t.status ?? 'unknown';
    counts[status] = (counts[status] ?? 0) + 1;
  }
  return counts;
}

/** Recount queue aggregates from task list (queue_manager.__recount_queue_aggregates). */
export function recountQueueAggregates(tasks: Task[]): QueueStats {
  const c = countByStatus(tasks);
  return {
    taskCount: tasks.length,
    pendingCount: c.pending ?? 0,
    processingCount: c.processing ?? 0,
    completedCount: c.completed ?? 0,
    failedCount: c.failed ?? 0,
    cancelledCount: c.cancelled ?? 0,
  };
}

/** Dataset task progress (queue_manager.__dataset_tasks_progress). */
export function datasetTasksProgress(tasks: Task[]): TaskProgress {
  const c = countByStatus(tasks);
  const total = tasks.length;
  const completed = c.completed ?? 0;
  const failed = c.failed ?? 0;
  const processing = c.processing ?? 0;
  const pending = c.pending ?? 0;
  const cancelled = c.cancelled ?? 0;

  let status: string;
  if (total === 0) {
    status = 'no_tasks';
  } else if (processing > 0 || pending > 0) {
    status = 'in_progress';
  } else if (completed > 0 && failed === 0 && processing === 0 && pending === 0) {
    status = 'completed';
  } else if (failed > 0 && processing === 0 && pending === 0) {
    status = 'completed_with_failures';
  } else {
    status = 'unknown';
  }

  const progressPct = total > 0 ? Math.round((completed / total) * 1000) / 10 : 0;

  return {
    total,
    completed,
    failed,
    processing,
    pending,
    cancelled,
    status,
    progressPct,
  };
}

export function parseValidationScore(scoreStr: unknown): [number | null, number | null] {
  try {
    if (!scoreStr) return [null, null];
    const parts = String(scoreStr).split('/');
    if (parts.length !== 2) return [null, null];
    return [parseFloat(parts[0]), parseFloat(parts[1])];
  } catch {
    return [null, null];
  }
}

export function toBool(val: unknown): boolean | null {
  if (typeof val === 'boolean') return val;
  if (typeof val === 'number') return Boolean(val);
  if (typeof val === 'string') {
    const v = val.trim().toLowerCase();
    if (['true', 'passed', 'yes', '1'].includes(v)) return true;
    if (['false', 'failed', 'no', '0'].includes(v)) return false;
  }
  return null;
}

/** Whether a question passed validation (statistics._is_passed). */
export function isValidationPassed(q: Question): boolean | null {
  const vp = toBool(q.validation_passed);
  if (vp !== null) return vp;
  const [total] = parseValidationScore(q.validation_score);
  let thr: number | null = null;
  try {
    const raw = q.validation_threshold;
    if (raw != null && raw !== 'N/A' && raw !== '') {
      thr = parseFloat(String(raw));
    }
  } catch {
    thr = null;
  }
  if (total !== null && thr !== null) return total >= thr;
  return null;
}

/** Statistics progress for a dataset (statistics.py + queue_manager.py formulas). */
export function computeDatasetProgress(
  dataset: Dataset,
  dsFull: Dataset | null,
  tasks: Task[],
): {
  status: string;
  progressPercent: number;
  expectedQuestions: number;
  totalQuestionsGenerated: number;
} {
  const metadata: DatasetMetadata = (dsFull ?? dataset).metadata ?? {};
  const questions = (dsFull ?? dataset).questions ?? [];
  const dsProg = datasetTasksProgress(tasks);

  const questionTypes = metadata.question_types ?? [];
  const questionsPerChunk = questionTypes.length || 1;
  const totalChunks = metadata.total_chunks ?? dataset.chunks_count ?? 0;
  const chunksForExpected = metadata.chunks_passed_gate || totalChunks;
  const totalQuestionsGenerated = metadata.total_questions_generated ?? questions.length;

  let expectedQuestions = metadata.expected_questions ?? 0;
  if (!expectedQuestions) {
    if (dsProg.total > 0) {
      expectedQuestions = dsProg.total;
    } else {
      expectedQuestions = chunksForExpected
        ? chunksForExpected * questionsPerChunk
        : totalQuestionsGenerated;
    }
  }

  let status = metadata.status ?? 'unknown';
  let progressPercent: number;

  if (dsProg.total === 0 && questions.length > 0) {
    status = metadata.status ?? 'completed';
    if (status === 'completed') {
      progressPercent = 100;
    } else {
      progressPercent = expectedQuestions
        ? Math.round((totalQuestionsGenerated / expectedQuestions) * 1000) / 10
        : 100;
    }
  } else if (dsProg.total > 0) {
    status = dsProg.status;
    progressPercent = dsProg.progressPct;
  } else if (metadata.status === 'completed') {
    progressPercent = 100;
  } else {
    progressPercent = expectedQuestions
      ? Math.round((totalQuestionsGenerated / expectedQuestions) * 1000) / 10
      : 0;
  }

  return {
    status,
    progressPercent: Math.min(100, progressPercent),
    expectedQuestions,
    totalQuestionsGenerated,
  };
}

/** Check if any dataset has active generation (bot._any_active_generation). */
export function anyActiveGeneration(
  datasets: Dataset[],
  tasksByDataset: Record<string, Task[]>,
): { active: boolean; queueName: string } {
  for (const ds of datasets) {
    const meta = ds.metadata ?? {};
    if (meta.status !== 'processing') continue;
    const dsId = ds._id;
    if (!dsId) continue;
    const tasks = tasksByDataset[dsId] ?? [];
    for (const t of tasks) {
      if (t.status === 'pending' || t.status === 'processing') {
        const qname = meta.queue_name || t.queue_name || '';
        return { active: true, queueName: qname };
      }
    }
  }
  return { active: false, queueName: '' };
}

/** Whether queue manager should poll (active tasks present). */
export function hasActiveWork(queues: { pending_count?: number; processing_count?: number }[]): boolean {
  return queues.some(
    (q) => (q.pending_count ?? 0) > 0 || (q.processing_count ?? 0) > 0,
  );
}
