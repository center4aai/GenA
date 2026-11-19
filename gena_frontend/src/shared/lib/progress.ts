import type {
  Dataset,
  DatasetMetadata,
  PipelineStats,
  Queue,
  Question,
  Task,
  TaskProgress,
  QueueStats,
} from '../types/documents';


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

/**
 * Queue aggregates as the server counted them.
 *
 * `GET /queues/` groups every task by status in Mongo, so these are true totals
 * for the whole queue — unlike recountQueueAggregates, which can only see the
 * rows a caller happened to fetch.
 */
export function queueStatsFromQueue(queue: Queue | null | undefined): QueueStats {
  return {
    taskCount: queue?.task_count ?? 0,
    pendingCount: queue?.pending_count ?? 0,
    processingCount: queue?.processing_count ?? 0,
    completedCount: queue?.completed_count ?? 0,
    failedCount: queue?.failed_count ?? 0,
    cancelledCount: queue?.cancelled_count ?? 0,
  };
}

/** The same shape from a pipeline-stats `by_status` map (one pipeline stage). */
export function queueStatsFromByStatus(by: Record<string, number> | undefined): QueueStats {
  const c = by ?? {};
  const counted = Object.values(c).reduce((n, v) => n + v, 0);
  return {
    taskCount: counted,
    pendingCount: c.pending ?? 0,
    processingCount: c.processing ?? 0,
    completedCount: c.completed ?? 0,
    failedCount: c.failed ?? 0,
    cancelledCount: c.cancelled ?? 0,
  };
}

export type GenerationStatus =
  | 'in_progress'
  | 'completed'
  | 'completed_with_failures'
  | 'cancelled';

/** Queue-level status shown in the generations table. */
export function generationStatus(stats: QueueStats): GenerationStatus {
  if (stats.pendingCount > 0 || stats.processingCount > 0) return 'in_progress';
  if (stats.taskCount > 0 && stats.cancelledCount === stats.taskCount) return 'cancelled';
  return stats.failedCount > 0 ? 'completed_with_failures' : 'completed';
}

/**
 * Percent for the generations-table mini bar, or null when there is nothing to
 * measure against.
 *
 * `expected_questions` is never decremented when a task fails, so a plain
 * done/expected ratio stays below 100% forever while the in-run stepper — which
 * counts terminal tasks — already shows "Done!". Failed and cancelled tasks are
 * therefore dropped from the denominator so the two views agree — but only once
 * the run has stopped: the queue-wide failed/cancelled counts include chunk and
 * gate tasks that were never part of `expected_questions`, so subtracting them
 * mid-run inflates the ratio to 100% while work is still going.
 */
export function generationProgressPercent(
  questionsDone: number | null,
  questionsExpected: number | null,
  stats: QueueStats,
): number | null {
  if (questionsDone == null || questionsExpected == null || questionsExpected <= 0) return null;
  if (generationStatus(stats) === 'in_progress') {
    return Math.min(99, Math.floor((questionsDone / questionsExpected) * 100));
  }
  const effectiveExpected = Math.max(
    0,
    questionsExpected - stats.failedCount - stats.cancelledCount,
  );
  if (effectiveExpected <= 0) return 100;
  return Math.min(100, Math.round((questionsDone / effectiveExpected) * 100));
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
  // Legacy skipped still counts as terminal for progress % only.
  const legacySkipped = c.skipped ?? 0;

  let status: string;
  if (total === 0) {
    status = 'no_tasks';
  } else if (processing > 0 || pending > 0) {
    status = 'in_progress';
  } else if (failed > 0) {
    status = 'completed_with_failures';
  } else if (completed > 0 || legacySkipped > 0 || cancelled > 0) {
    status = 'completed';
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
    const score = parseFloat(parts[0]);
    const max = parseFloat(parts[1]);
    if (Number.isNaN(score) || Number.isNaN(max)) return [null, null];
    return [score, max];
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
  const chunksPassedGate = metadata.chunks_passed_gate;
  const chunksForExpected =
    chunksPassedGate != null && chunksPassedGate > 0 ? chunksPassedGate : totalChunks;
  const totalQuestionsGenerated = metadata.total_questions_generated ?? questions.length;

  let expectedQuestions = metadata.expected_questions ?? 0;
  if (!expectedQuestions) {
    const genTasks = tasks.filter((t) => t.task_type !== 'gate' && t.task_type !== 'chunk');
    if (genTasks.length > 0) {
      expectedQuestions = genTasks.length;
    } else {
      expectedQuestions = chunksForExpected
        ? chunksForExpected * questionsPerChunk
        : totalQuestionsGenerated;
    }
  }

  let status = metadata.status ?? 'unknown';
  let progressPercent: number;

  // Prefer questions_done / expected_questions so "completed" metadata cannot
  // force a misleading 100% when many tasks were skipped/failed.
  const questionRatioPct = (expected: number, done: number) =>
    expected > 0 ? Math.round((done / expected) * 1000) / 10 : 0;

  if (dsProg.total === 0 && questions.length > 0) {
    status = metadata.status ?? 'completed';
    const expectedForProgress = chunksForExpected
      ? chunksForExpected * questionsPerChunk
      : totalQuestionsGenerated || expectedQuestions;
    progressPercent = expectedForProgress
      ? questionRatioPct(expectedForProgress, totalQuestionsGenerated)
      : 100;
    } else if (dsProg.total > 0) {
    status = dsProg.status;
    if (metadata.status === 'no_valid_chunks' || isNoValidChunks(tasks, metadata)) {
      status = 'no_valid_chunks';
    } else if (dsProg.status !== 'in_progress' && (metadata.status === 'completed_with_failures' || dsProg.failed > 0)) {
      status = 'completed_with_failures';
    } else if (metadata.status === 'completed' && dsProg.pending === 0 && dsProg.processing === 0) {
      status = dsProg.failed > 0 ? 'completed_with_failures' : 'completed';
    }
    if (expectedQuestions > 0) {
      progressPercent = questionRatioPct(expectedQuestions, totalQuestionsGenerated);
    } else {
      const terminal =
        dsProg.completed + dsProg.failed + dsProg.cancelled;
      progressPercent =
        dsProg.total > 0 ? Math.round((terminal / dsProg.total) * 1000) / 10 : 0;
    }
  } else if (
    metadata.status === 'completed' ||
    metadata.status === 'completed_with_failures' ||
    metadata.status === 'no_valid_chunks'
  ) {
    progressPercent = expectedQuestions
      ? questionRatioPct(expectedQuestions, totalQuestionsGenerated)
      : totalQuestionsGenerated > 0
        ? 100
        : 0;
    if (metadata.status === 'completed_with_failures') status = 'completed_with_failures';
    if (metadata.status === 'no_valid_chunks') status = 'no_valid_chunks';
  } else {
    progressPercent = expectedQuestions
      ? questionRatioPct(expectedQuestions, totalQuestionsGenerated)
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

// ── Queue pipeline phases (Chunking → Gate → Generation → Done) ──

export type PipelinePhaseKey = 'chunking' | 'gate' | 'generation' | 'done';
export type PipelinePhaseState = 'completed' | 'active' | 'pending' | 'failed' | 'disabled';

export interface PipelinePhase {
  key: PipelinePhaseKey;
  state: PipelinePhaseState;
  /**
   * Units that stopped moving, failures included. Decides `state` and `fraction`
   * so a run that ends with failures still fills its bar and reaches "Done!".
   */
  done: number;
  /**
   * Units that actually produced something. This is what the stepper prints:
   * a failed generation task yielded no question, so counting it as progress
   * made the label disagree with the questions card beside it.
   */
  completed: number;
  total: number;
  /** 0..1 progress inside the phase (for the connector fill). */
  fraction: number;
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled', 'skipped']);

function isGateRejection(task: Task): boolean {
  if (task.gate_passed === false) return true;
  const code = task.message_code ?? '';
  if (code.startsWith('gate_rejected_')) return true;
  if (task.status === 'skipped') {
    const err = task.error ?? '';
    if (err.startsWith('chunk_rejected') || task.task_type === 'gate') return true;
  }
  return false;
}

/** True when the pipeline mode disables the gate (ablation generator_validator). */
export function isGateDisabledByMode(metadata?: DatasetMetadata): boolean {
  if (!metadata) return false;
  if (metadata.gate_enabled === false) return true;
  if (metadata.gate_enabled === true) return false;
  return metadata.pipeline_mode === 'generator_validator';
}

/** True when async gate finished and rejected every chunk (nothing to generate). */
export function isNoValidChunks(
  tasks: Task[],
  metadata?: DatasetMetadata,
): boolean {
  if (metadata?.status === 'no_valid_chunks') return true;
  const gateTasks = tasks.filter((t) => t.task_type === 'gate');
  const genTasks = tasks.filter((t) => t.task_type !== 'gate' && t.task_type !== 'chunk');
  if (!gateTasks.length || genTasks.length > 0) return false;
  const gateDone = gateTasks.every((t) => TERMINAL_STATUSES.has(t.status ?? ''));
  if (!gateDone) return false;
  const allRejected = gateTasks.every((t) => isGateRejection(t));
  const rejected = metadata?.chunks_rejected ?? 0;
  const passed = metadata?.chunks_passed_gate ?? 0;
  return allRejected || (rejected > 0 && passed === 0);
}

/**
 * Compute unified pipeline phases for one queue.
 *
 * Gate tasks (`task_type === 'gate'`) validate chunks in the background and fan
 * out generation tasks, so generation totals grow while the gate phase runs.
 */
export function computeQueuePipeline(
  tasks: Task[],
  metadata?: DatasetMetadata,
): PipelinePhase[] {
  const chunkTasks = tasks.filter((t) => t.task_type === 'chunk');
  const gateTasks = tasks.filter((t) => t.task_type === 'gate');
  const genTasks = tasks.filter((t) => t.task_type !== 'gate' && t.task_type !== 'chunk');

  const gateDone = gateTasks.filter((t) => TERMINAL_STATUSES.has(t.status ?? '')).length;
  const genDone = genTasks.filter((t) => TERMINAL_STATUSES.has(t.status ?? '')).length;
  // A gate task that rejects its chunk still completes, so gate work done and
  // gate work succeeded are the same number. Generation is where they diverge.
  const gateCompleted = gateTasks.filter((t) => t.status === 'completed').length;
  const genCompleted = genTasks.filter((t) => t.status === 'completed').length;

  const totalChunks = metadata?.total_chunks ?? 0;
  const noValid = isNoValidChunks(tasks, metadata);
  const gateDisabled = isGateDisabledByMode(metadata);

  // Chunking runs asynchronously in the queue (a `chunk` task). While it is
  // in flight the later phases have no tasks yet and stay pending.
  const chunkDone = chunkTasks.filter((t) => TERMINAL_STATUSES.has(t.status ?? '')).length;
  const chunkActive = chunkTasks.length > 0 && chunkDone < chunkTasks.length;
  const chunking: PipelinePhase =
    chunkTasks.length > 0
      ? {
          key: 'chunking',
          state: chunkActive ? 'active' : 'completed',
          done: chunkActive ? 0 : totalChunks,
          completed: chunkActive ? 0 : totalChunks,
          total: totalChunks || chunkTasks.length,
          fraction: chunkActive ? 0 : 1,
        }
      : {
          key: 'chunking',
          state: 'completed',
          done: totalChunks,
          completed: totalChunks,
          total: totalChunks,
          fraction: 1,
        };

  let gate: PipelinePhase;
  if (gateDisabled) {
    gate = {
      key: 'gate',
      state: 'disabled',
      done: totalChunks,
      completed: totalChunks,
      total: totalChunks,
      fraction: 1,
    };
  } else if (gateTasks.length > 0) {
    gate = {
      key: 'gate',
      state: gateDone >= gateTasks.length ? 'completed' : 'active',
      done: gateDone,
      completed: gateCompleted,
      total: gateTasks.length,
      fraction: gateTasks.length ? gateDone / gateTasks.length : 1,
    };
  } else {
    // Sync gate (verdicts stored before enqueue).
    const passed = metadata?.chunks_passed_gate ?? 0;
    const rejected = metadata?.chunks_rejected ?? 0;
    gate = {
      key: 'gate',
      state: 'completed',
      done: passed + rejected || totalChunks,
      completed: passed + rejected || totalChunks,
      total: passed + rejected || totalChunks,
      fraction: 1,
    };
  }

  const gateFinished = gate.state === 'completed' || gate.state === 'disabled';
  // While the gate is running, more generation tasks may still appear.
  const genTotal = genTasks.length;
  let genState: PipelinePhaseState;
  if (noValid && gateFinished) {
    genState = 'failed';
  } else if (gateFinished) {
    genState = genTotal === 0 || genDone >= genTotal ? 'completed' : 'active';
  } else {
    genState = genTotal > 0 ? 'active' : 'pending';
  }
  const generation: PipelinePhase = {
    key: 'generation',
    state: genState,
    done: genDone,
    completed: genCompleted,
    total: genTotal,
    fraction: genTotal > 0 ? genDone / genTotal : gateFinished ? 1 : 0,
  };

  const allFinished =
    !chunkActive &&
    gateFinished &&
    (generation.state === 'completed' || generation.state === 'failed');
  const done: PipelinePhase = {
    key: 'done',
    // Keep "done" pending when generation failed due to no valid chunks —
    // the run finished, but not successfully.
    state: allFinished && generation.state === 'completed' ? 'completed' : allFinished ? 'failed' : 'pending',
    done: allFinished && generation.state === 'completed' ? 1 : 0,
    completed: allFinished && generation.state === 'completed' ? 1 : 0,
    total: 1,
    fraction: allFinished && generation.state === 'completed' ? 1 : 0,
  };

  // While chunking is still running there are no gate/generation tasks yet, so
  // force those phases to "pending" instead of the empty-list "completed" state.
  if (chunkActive) {
    if (!gateDisabled) {
      gate = { ...gate, state: 'pending', done: 0, completed: 0, fraction: 0 };
    }
    generation.state = 'pending';
    generation.done = 0;
    generation.completed = 0;
    generation.fraction = 0;
  }

  return [chunking, gate, generation, done];
}

/**
 * The same four phases, built from the server's own counts.
 *
 * Preferred over computeQueuePipeline whenever the queue has a dataset behind
 * it. The page can only hold one page of tasks at a time, so counting them in
 * the browser silently reported whatever slice happened to be loaded — on a run
 * with thousands of tasks that meant a denominator made of the fetch limit
 * rather than of the work. pipeline-stats counts the whole run in Mongo.
 */
// With zero tasks the ratio is undefined: a phase that finished (or was
// skipped) with no tasks legitimately counts as full, but a pending/active one
// must stay empty — otherwise the stepper paints future connectors green while
// chunking is still running.
function emptyPhaseFraction(state: PipelinePhaseState): number {
  return state === 'completed' || state === 'disabled' || state === 'failed' ? 1 : 0;
}

export function pipelineFromStats(stats: PipelineStats): PipelinePhase[] {
  const gen = stats.generation;
  const genTerminal = gen.done;
  // expected_questions is bumped in the same step that enqueues the tasks, so
  // the two agree; max() only covers the instant between enqueue and bump, and
  // an older API that does not send `total` yet.
  const genTotal = Math.max(gen.total ?? 0, gen.expected_questions ?? 0);
  const genCompleted = gen.completed ?? gen.by_status?.completed ?? 0;

  const gateTotal = stats.gate.total || 0;
  const chunkTotal = stats.chunking.total || stats.chunking.total_chunks || 1;

  const phases: PipelinePhase[] = [
    {
      key: 'chunking',
      state: stats.chunking.state,
      done: stats.chunking.done,
      completed: stats.chunking.done,
      total: chunkTotal,
      fraction: chunkTotal > 0 ? stats.chunking.done / chunkTotal : 1,
    },
    {
      key: 'gate',
      state: stats.gate.state,
      done: stats.gate.done,
      // A rejected chunk is still a gate task that did its job.
      completed: stats.gate.done,
      total: gateTotal,
      fraction: gateTotal > 0 ? stats.gate.done / gateTotal : emptyPhaseFraction(stats.gate.state),
    },
    {
      key: 'generation',
      state: gen.state,
      done: genTerminal,
      completed: genCompleted,
      total: genTotal,
      fraction: genTotal > 0 ? genTerminal / genTotal : emptyPhaseFraction(gen.state),
    },
  ];

  const genFinished = gen.state === 'completed' || gen.state === 'failed';
  const allFinished =
    stats.chunking.state === 'completed'
    && (stats.gate.state === 'completed' || stats.gate.state === 'disabled')
    && genFinished;
  const succeeded = allFinished && gen.state === 'completed';
  phases.push({
    key: 'done',
    state: succeeded ? 'completed' : allFinished ? 'failed' : 'pending',
    done: succeeded ? 1 : 0,
    completed: succeeded ? 1 : 0,
    total: 1,
    fraction: succeeded ? 1 : 0,
  });

  return phases;
}

/** Tasks belonging to one pipeline stage, using the same task-type split as
 * computeQueuePipeline (generation = everything that isn't chunk or gate). */
export function tasksForStage(tasks: Task[], stage: PipelinePhaseKey): Task[] {
  if (stage === 'chunking') return tasks.filter((t) => t.task_type === 'chunk');
  if (stage === 'gate') return tasks.filter((t) => t.task_type === 'gate');
  if (stage === 'generation') {
    return tasks.filter((t) => t.task_type !== 'gate' && t.task_type !== 'chunk');
  }
  return tasks;
}

/**
 * The stage whose statistics should be shown right now: the first active phase
 * (chunking → gate → generation). When nothing is active (all done or nothing
 * started yet) fall back to the last meaningful stage that has tasks, so a
 * finished queue shows the final question numbers.
 */
export function currentStageKey(phases: PipelinePhase[]): PipelinePhaseKey {
  const order: PipelinePhaseKey[] = ['chunking', 'gate', 'generation'];
  const active = order.find((k) => phases.find((p) => p.key === k)?.state === 'active');
  if (active) return active;
  const gen = phases.find((p) => p.key === 'generation');
  if (gen && gen.total > 0) return 'generation';
  const gate = phases.find((p) => p.key === 'gate');
  if (gate && gate.total > 0) return 'gate';
  return 'chunking';
}

/** Whether queue manager should poll (active tasks present). */
export function hasActiveWork(queues: { pending_count?: number; processing_count?: number }[]): boolean {
  return queues.some(
    (q) => (q.pending_count ?? 0) > 0 || (q.processing_count ?? 0) > 0,
  );
}
