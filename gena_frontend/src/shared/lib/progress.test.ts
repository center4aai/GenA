import { describe, expect, it } from 'vitest';
import {
  computeDatasetProgress,
  computeQueuePipeline,
  currentStageKey,
  datasetTasksProgress,
  generationProgressPercent,
  generationStatus,
  isValidationPassed,
  parseValidationScore,
  pipelineFromStats,
  queueStatsFromByStatus,
  queueStatsFromQueue,
  recountQueueAggregates,
  tasksForStage,
  toBool,
} from './progress';
import type { PipelineStats, QueueStats, Question, Task } from '../types/documents';

function stats(over: Partial<QueueStats> = {}): QueueStats {
  return {
    taskCount: 0,
    pendingCount: 0,
    processingCount: 0,
    completedCount: 0,
    failedCount: 0,
    cancelledCount: 0,
    ...over,
  };
}

describe('generationStatus', () => {
  it('is in_progress while any task is pending or processing', () => {
    expect(generationStatus(stats({ taskCount: 3, pendingCount: 1 }))).toBe('in_progress');
    expect(generationStatus(stats({ taskCount: 3, processingCount: 1 }))).toBe('in_progress');
  });

  it('separates a clean finish from one with failures', () => {
    expect(generationStatus(stats({ taskCount: 3, completedCount: 3 }))).toBe('completed');
    expect(generationStatus(stats({ taskCount: 3, completedCount: 2, failedCount: 1 }))).toBe(
      'completed_with_failures',
    );
  });

  it('reports cancelled only when every task was cancelled', () => {
    expect(generationStatus(stats({ taskCount: 2, cancelledCount: 2 }))).toBe('cancelled');
    expect(generationStatus(stats({ taskCount: 2, cancelledCount: 1, completedCount: 1 }))).toBe(
      'completed',
    );
  });
});

describe('generationProgressPercent', () => {
  it('reaches 100% once the only shortfall is failed tasks', () => {
    // The reported case: the stepper says "Done!", the table used to say 98%.
    const s = stats({ taskCount: 40, completedCount: 39, failedCount: 1 });
    expect(generationProgressPercent(39, 40, s)).toBe(100);
  });

  it('discounts cancelled tasks the same way', () => {
    const s = stats({ taskCount: 40, completedCount: 38, cancelledCount: 2 });
    expect(generationProgressPercent(38, 40, s)).toBe(100);
  });

  it('still tracks real progress while the run is going', () => {
    const s = stats({ taskCount: 40, completedCount: 10, failedCount: 1, pendingCount: 29 });
    // Failures are not discounted mid-run: the queue-wide failed count also
    // covers chunk/gate tasks, which are not questions.
    expect(generationProgressPercent(10, 40, s)).toBe(25); // 10 / 40, floored
  });

  it('never shows 100% while the run is still in progress', () => {
    // Regression: 13 queue-wide failures (chunk/gate included) used to shrink
    // the denominator to 1233, so 1228/1233 rounded up to 100% mid-run.
    const s = stats({
      taskCount: 1897,
      completedCount: 1866,
      failedCount: 13,
      pendingCount: 16,
      processingCount: 2,
    });
    expect(generationProgressPercent(1228, 1246, s)).toBe(98);
  });

  it('caps at 99% mid-run even when done already matches expected', () => {
    const s = stats({ taskCount: 41, completedCount: 40, pendingCount: 1 });
    expect(generationProgressPercent(40, 40, s)).toBe(99);
  });

  it('is 100% when every task failed and the run has stopped', () => {
    const s = stats({ taskCount: 40, failedCount: 40 });
    expect(generationProgressPercent(0, 40, s)).toBe(100);
  });

  it('stays null when nothing is expected yet', () => {
    expect(generationProgressPercent(0, 0, stats())).toBeNull();
    expect(generationProgressPercent(null, 40, stats())).toBeNull();
    expect(generationProgressPercent(5, null, stats())).toBeNull();
  });

  it('never exceeds 100% when failures outrun the expectation', () => {
    const s = stats({ taskCount: 5, completedCount: 3, failedCount: 4 });
    expect(generationProgressPercent(3, 5, s)).toBe(100);
  });
});

function task(id: string, status: string, taskType = 'generation'): Task {
  return { _id: id, status, task_type: taskType };
}

describe('recountQueueAggregates', () => {
  it('counts mixed task statuses', () => {
    const tasks: Task[] = [
      task('1', 'pending'),
      task('2', 'pending'),
      task('3', 'processing'),
      task('4', 'completed'),
      task('5', 'completed'),
      task('6', 'failed'),
      task('7', 'cancelled'),
    ];
    expect(recountQueueAggregates(tasks)).toEqual({
      taskCount: 7,
      pendingCount: 2,
      processingCount: 1,
      completedCount: 2,
      failedCount: 1,
      cancelledCount: 1,
    });
  });
});

describe('datasetTasksProgress', () => {
  it('returns no_tasks when empty', () => {
    expect(datasetTasksProgress([])).toMatchObject({
      status: 'no_tasks',
      total: 0,
      progressPct: 0,
    });
  });

  it('returns in_progress when pending or processing remain', () => {
    expect(datasetTasksProgress([task('1', 'pending')]).status).toBe('in_progress');
    expect(datasetTasksProgress([task('1', 'processing')]).status).toBe('in_progress');
    expect(
      datasetTasksProgress([task('1', 'completed'), task('2', 'pending')]).status,
    ).toBe('in_progress');
  });

  it('returns completed_with_failures when failed and no active tasks', () => {
    const prog = datasetTasksProgress([
      task('1', 'completed'),
      task('2', 'failed'),
    ]);
    expect(prog.status).toBe('completed_with_failures');
    expect(prog.failed).toBe(1);
  });

  it('returns completed when only completed remain', () => {
    expect(
      datasetTasksProgress([task('1', 'completed'), task('2', 'completed')]).status,
    ).toBe('completed');
  });

  it('returns completed for legacy skipped rows', () => {
    expect(
      datasetTasksProgress([task('1', 'completed'), task('2', 'skipped')]).status,
    ).toBe('completed');
  });

  it('rounds progressPct to one decimal place', () => {
    const prog = datasetTasksProgress([
      task('1', 'completed'),
      task('2', 'pending'),
      task('3', 'pending'),
    ]);
    expect(prog.progressPct).toBe(33.3);
  });
});

describe('computeQueuePipeline', () => {
  it('async gate in progress with generation tasks appearing', () => {
    const gateTasks = [
      ...['g1', 'g2', 'g3', 'g4'].map((id) => task(id, 'completed', 'gate')),
      ...['g5', 'g6', 'g7', 'g8', 'g9', 'g10'].map((id) => task(id, 'processing', 'gate')),
    ];
    const genTasks = [
      task('n1', 'completed'),
      task('n2', 'pending'),
      task('n3', 'processing'),
    ];
    const phases = computeQueuePipeline([...gateTasks, ...genTasks], { total_chunks: 10 });

    expect(phases[0]).toMatchObject({ key: 'chunking', state: 'completed', done: 10, total: 10 });
    expect(phases[1]).toMatchObject({
      key: 'gate',
      state: 'active',
      done: 4,
      total: 10,
      fraction: 0.4,
    });
    expect(phases[2]).toMatchObject({
      key: 'generation',
      state: 'active',
      done: 1,
      total: 3,
    });
    expect(phases[3]).toMatchObject({ key: 'done', state: 'pending', done: 0, total: 1 });
  });

  it('gate finished with generation running', () => {
    const gateTasks = Array.from({ length: 10 }, (_, i) =>
      task(`g${i}`, 'completed', 'gate'),
    );
    const genTasks = [
      ...Array.from({ length: 5 }, (_, i) => task(`n${i}`, 'completed')),
      ...Array.from({ length: 5 }, (_, i) => task(`p${i}`, 'pending')),
    ];
    const phases = computeQueuePipeline([...gateTasks, ...genTasks]);

    expect(phases[1]).toMatchObject({ key: 'gate', state: 'completed', done: 10, total: 10 });
    expect(phases[2]).toMatchObject({
      key: 'generation',
      state: 'active',
      done: 5,
      total: 10,
      fraction: 0.5,
    });
    expect(phases[3]).toMatchObject({ key: 'done', state: 'pending' });
  });

  it('all phases finished when gate and generation complete', () => {
    const gateTasks = Array.from({ length: 4 }, (_, i) =>
      task(`g${i}`, 'completed', 'gate'),
    );
    const genTasks = Array.from({ length: 6 }, (_, i) =>
      task(`n${i}`, 'completed'),
    );
    const phases = computeQueuePipeline([...gateTasks, ...genTasks], { total_chunks: 4 });

    expect(phases[1].state).toBe('completed');
    expect(phases[2]).toMatchObject({ key: 'generation', state: 'completed', done: 6, total: 6 });
    expect(phases[3]).toMatchObject({
      key: 'done',
      state: 'completed',
      done: 1,
      total: 1,
      fraction: 1,
    });
  });

  it('separates ready questions from tasks that merely stopped', () => {
    const gateTasks = Array.from({ length: 4 }, (_, i) => task(`g${i}`, 'completed', 'gate'));
    const genTasks = [
      ...Array.from({ length: 5 }, (_, i) => task(`n${i}`, 'completed')),
      task('f1', 'failed'),
    ];
    const phases = computeQueuePipeline([...gateTasks, ...genTasks], { total_chunks: 4 });

    const gen = phases[2];
    expect(gen.completed).toBe(5);   // shown to the user
    expect(gen.done).toBe(6);        // drives state/fraction
    expect(gen.state).toBe('completed');
    expect(gen.fraction).toBe(1);
  });

  it('all chunks rejected: generation and done mark failed with zero gen tasks', () => {
    const gateTasks = Array.from({ length: 5 }, (_, i) => ({
      _id: `g${i}`,
      status: 'completed',
      task_type: 'gate',
      gate_passed: false,
    })) as Task[];
    const phases = computeQueuePipeline(gateTasks);

    expect(phases[1]).toMatchObject({ key: 'gate', state: 'completed', done: 5, total: 5 });
    expect(phases[2]).toMatchObject({
      key: 'generation',
      state: 'failed',
      done: 0,
      total: 0,
      fraction: 1,
    });
    expect(phases[3]).toMatchObject({ key: 'done', state: 'failed' });
  });

  it('metadata status no_valid_chunks forces failed generation phase', () => {
    const phases = computeQueuePipeline([], {
      status: 'no_valid_chunks',
      total_chunks: 3,
      chunks_passed_gate: 0,
      chunks_rejected: 3,
    });
    expect(phases[2].state).toBe('failed');
    expect(phases[3].state).toBe('failed');
  });

  it('sync-gate queue uses metadata chunk counts', () => {
    const phases = computeQueuePipeline([], {
      total_chunks: 70,
      chunks_passed_gate: 50,
      chunks_rejected: 20,
    });

    expect(phases[1]).toMatchObject({
      key: 'gate',
      state: 'completed',
      done: 70,
      total: 70,
      fraction: 1,
    });
  });

  it('chunking phase always completed from metadata.total_chunks', () => {
    const phases = computeQueuePipeline([], { total_chunks: 42 });
    expect(phases[0]).toMatchObject({
      key: 'chunking',
      state: 'completed',
      done: 42,
      total: 42,
      fraction: 1,
    });
  });

  it('marks gate as disabled for generator_validator ablation mode', () => {
    const phases = computeQueuePipeline(
      [task('c1', 'completed', 'chunk'), task('n1', 'pending')],
      {
        total_chunks: 10,
        pipeline_mode: 'generator_validator',
        gate_enabled: false,
      },
    );
    expect(phases[1]).toMatchObject({ key: 'gate', state: 'disabled', fraction: 1 });
    expect(phases[2].state).toBe('active');
  });
});

describe('tasksForStage', () => {
  const tasks: Task[] = [
    task('c1', 'completed', 'chunk'),
    task('g1', 'completed', 'gate'),
    task('g2', 'processing', 'gate'),
    task('n1', 'pending'),
    task('n2', 'completed'),
  ];

  it('splits tasks by pipeline stage', () => {
    expect(tasksForStage(tasks, 'chunking').map((t) => t._id)).toEqual(['c1']);
    expect(tasksForStage(tasks, 'gate').map((t) => t._id)).toEqual(['g1', 'g2']);
    expect(tasksForStage(tasks, 'generation').map((t) => t._id)).toEqual(['n1', 'n2']);
  });

  it('generation excludes chunk and gate tasks', () => {
    expect(tasksForStage(tasks, 'generation')).toHaveLength(2);
  });
});

describe('currentStageKey', () => {
  it('picks gate while the gate is running', () => {
    const gateTasks = [
      ...['g1', 'g2'].map((id) => task(id, 'completed', 'gate')),
      ...['g3', 'g4'].map((id) => task(id, 'processing', 'gate')),
    ];
    const genTasks = [task('n1', 'pending')];
    const phases = computeQueuePipeline([...gateTasks, ...genTasks], { total_chunks: 4 });
    expect(currentStageKey(phases)).toBe('gate');
  });

  it('picks generation once the gate is done', () => {
    const gateTasks = Array.from({ length: 4 }, (_, i) => task(`g${i}`, 'completed', 'gate'));
    const genTasks = [task('n1', 'completed'), task('n2', 'pending')];
    const phases = computeQueuePipeline([...gateTasks, ...genTasks]);
    expect(currentStageKey(phases)).toBe('generation');
  });

  it('falls back to generation when everything is finished', () => {
    const gateTasks = Array.from({ length: 4 }, (_, i) => task(`g${i}`, 'completed', 'gate'));
    const genTasks = Array.from({ length: 6 }, (_, i) => task(`n${i}`, 'completed'));
    const phases = computeQueuePipeline([...gateTasks, ...genTasks], { total_chunks: 4 });
    expect(currentStageKey(phases)).toBe('generation');
  });

  it('picks chunking while chunking is in flight', () => {
    const phases = computeQueuePipeline([task('c1', 'processing', 'chunk')], { total_chunks: 0 });
    expect(currentStageKey(phases)).toBe('chunking');
  });
});

function pipelineStats(over: {
  generation?: Partial<PipelineStats['generation']>;
  gate?: Partial<PipelineStats['gate']>;
  chunking?: Partial<PipelineStats['chunking']>;
  models?: PipelineStats['models'];
} = {}): PipelineStats {
  return {
    dataset_id: 'd1',
    meta: {},
    chunking: { state: 'completed', total_chunks: 10, done: 10, total: 10, ...over.chunking },
    gate: {
      state: 'completed',
      total: 10,
      done: 10,
      passed: 8,
      rejected: 2,
      rejection_reasons: {},
      criteria: {
        c1_pass: 0, c2_pass: 0, c3_pass: 0,
        c1_avg_confidence: null, c2_avg_confidence: null, c3_avg_confidence: null,
        scored: 0,
      },
      question_types_valid: {},
      ...over.gate,
    },
    generation: {
      state: 'active',
      total: 24,
      done: 0,
      completed: 0,
      by_status: {},
      questions_generated: 0,
      expected_questions: 24,
      validation: { passed: 0, failed: 0, avg_score: null },
      ...over.generation,
    },
    models: over.models,
  };
}

describe('pipelineFromStats', () => {
  it('shows ready questions but measures progress by work that stopped', () => {
    const phases = pipelineFromStats(pipelineStats({
      generation: {
        state: 'active',
        total: 8716,
        done: 527,
        completed: 523,
        by_status: { completed: 523, failed: 4, pending: 8189 },
        expected_questions: 8716,
      },
    }));
    const gen = phases.find((p) => p.key === 'generation')!;
    // The label must not count the 4 failures as generated questions...
    expect(gen.completed).toBe(523);
    // ...but the bar still has to know they are done moving.
    expect(gen.done).toBe(527);
    expect(gen.total).toBe(8716);
    expect(gen.fraction).toBeCloseTo(527 / 8716);
  });

  it('a run that ends with failures still fills the bar and reaches done', () => {
    const phases = pipelineFromStats(pipelineStats({
      generation: {
        state: 'completed',
        total: 10,
        done: 10,
        completed: 7,
        by_status: { completed: 7, failed: 3 },
        expected_questions: 10,
      },
    }));
    const gen = phases.find((p) => p.key === 'generation')!;
    expect(gen.completed).toBe(7);
    expect(gen.fraction).toBe(1);
    expect(phases.find((p) => p.key === 'done')!.state).toBe('completed');
  });

  it('trusts the counted task total over a lagging expected_questions', () => {
    const phases = pipelineFromStats(pipelineStats({
      generation: { total: 30, expected_questions: 24 },
    }));
    expect(phases.find((p) => p.key === 'generation')!.total).toBe(30);
  });

  it('falls back to expected_questions when the API sends no total yet', () => {
    const phases = pipelineFromStats(pipelineStats({
      generation: { total: 0, expected_questions: 24 },
    }));
    expect(phases.find((p) => p.key === 'generation')!.total).toBe(24);
  });

  it('falls back to by_status when the API sends no completed yet', () => {
    const phases = pipelineFromStats(pipelineStats({
      generation: { completed: undefined, by_status: { completed: 5, failed: 1 } },
    }));
    expect(phases.find((p) => p.key === 'generation')!.completed).toBe(5);
  });

  it('passes phase states through, including a failed generation', () => {
    const phases = pipelineFromStats(pipelineStats({
      gate: { state: 'disabled' },
      generation: { state: 'failed', total: 0, done: 0, completed: 0, expected_questions: 0 },
    }));
    expect(phases.find((p) => p.key === 'gate')!.state).toBe('disabled');
    expect(phases.find((p) => p.key === 'generation')!.state).toBe('failed');
    expect(phases.find((p) => p.key === 'done')!.state).toBe('failed');
  });

  it('keeps future phases empty while chunking is still running', () => {
    // Before the gate/generation fan-out the server reports total: 0 for both;
    // the empty ratio must not read as "full" (the bar looked pre-filled).
    const phases = pipelineFromStats(pipelineStats({
      chunking: { state: 'active', total_chunks: 416, total: 0, done: 0 },
      gate: { state: 'pending', total: 0, done: 0, passed: 0, rejected: 0 },
      generation: { state: 'pending', total: 0, done: 0, completed: 0, expected_questions: 0 },
    }));
    expect(phases.find((p) => p.key === 'gate')!.fraction).toBe(0);
    expect(phases.find((p) => p.key === 'generation')!.fraction).toBe(0);
  });

  it('keeps generation empty while the gate runs and no tasks are enqueued yet', () => {
    const phases = pipelineFromStats(pipelineStats({
      gate: { state: 'active', total: 416, done: 2 },
      generation: { state: 'pending', total: 0, done: 0, completed: 0, expected_questions: 0 },
    }));
    expect(phases.find((p) => p.key === 'generation')!.fraction).toBe(0);
  });

  it('still counts a finished phase with zero tasks as full', () => {
    const phases = pipelineFromStats(pipelineStats({
      gate: { state: 'disabled', total: 0, done: 0 },
      generation: { state: 'completed', total: 0, done: 0, completed: 0, expected_questions: 0 },
    }));
    expect(phases.find((p) => p.key === 'gate')!.fraction).toBe(1);
    expect(phases.find((p) => p.key === 'generation')!.fraction).toBe(1);
  });

  it('is not done while the gate is still running', () => {
    const phases = pipelineFromStats(pipelineStats({
      gate: { state: 'active', done: 4, total: 10 },
      generation: { state: 'active' },
    }));
    expect(phases.find((p) => p.key === 'gate')!.fraction).toBeCloseTo(0.4);
    expect(phases.find((p) => p.key === 'done')!.state).toBe('pending');
  });
});

describe('queue stats from server counts', () => {
  it('reads the queue document rather than a fetched page', () => {
    expect(queueStatsFromQueue({
      name: 'q',
      task_count: 12532,
      pending_count: 8189,
      processing_count: 1,
      completed_count: 4338,
      failed_count: 4,
      cancelled_count: 0,
    })).toEqual({
      taskCount: 12532,
      pendingCount: 8189,
      processingCount: 1,
      completedCount: 4338,
      failedCount: 4,
      cancelledCount: 0,
    });
  });

  it('defaults to zeros for a queue with no counts', () => {
    expect(queueStatsFromQueue(null).taskCount).toBe(0);
  });

  it('totals a by_status map, unknown statuses included', () => {
    const s = queueStatsFromByStatus({ completed: 3, failed: 1, weird: 2 });
    expect(s.taskCount).toBe(6);
    expect(s.completedCount).toBe(3);
    expect(s.failedCount).toBe(1);
  });
});

describe('parseValidationScore', () => {
  it('parses score/max pair', () => {
    expect(parseValidationScore('8.5/10')).toEqual([8.5, 10]);
  });

  it('returns nulls for invalid input', () => {
    expect(parseValidationScore('')).toEqual([null, null]);
    expect(parseValidationScore('bad')).toEqual([null, null]);
    expect(parseValidationScore('1/2/3')).toEqual([null, null]);
  });
});

describe('toBool', () => {
  it('coerces booleans and numbers', () => {
    expect(toBool(true)).toBe(true);
    expect(toBool(false)).toBe(false);
    expect(toBool(1)).toBe(true);
    expect(toBool(0)).toBe(false);
  });

  it('parses common string truthy/falsy values', () => {
    expect(toBool('passed')).toBe(true);
    expect(toBool('FALSE')).toBe(false);
    expect(toBool(' yes ')).toBe(true);
  });

  it('returns null for unrecognized values', () => {
    expect(toBool('maybe')).toBeNull();
    expect(toBool(null)).toBeNull();
  });
});

describe('isValidationPassed', () => {
  it('prefers validation_passed when set', () => {
    expect(isValidationPassed({ validation_passed: true } as Question)).toBe(true);
    expect(isValidationPassed({ validation_passed: 'failed' } as Question)).toBe(false);
  });

  it('compares validation_score against threshold', () => {
    const q: Question = {
      validation_score: '7/10',
      validation_threshold: '6',
    };
    expect(isValidationPassed(q)).toBe(true);

    const fail: Question = {
      validation_score: '5/10',
      validation_threshold: '6',
    };
    expect(isValidationPassed(fail)).toBe(false);
  });

  it('returns null when score or threshold missing', () => {
    expect(isValidationPassed({ validation_score: '7/10' } as Question)).toBeNull();
  });
});

describe('computeDatasetProgress', () => {
  it('keeps in_progress when some tasks failed but work remains', () => {
    const dataset = {
      _id: 'ds1',
      name: 'ds',
      metadata: { status: 'processing', expected_questions: 10, total_questions_generated: 2 },
      questions: [],
    };
    const tasks: Task[] = [
      task('1', 'completed'),
      task('2', 'failed'),
      task('3', 'pending'),
    ];
    const prog = computeDatasetProgress(dataset as never, dataset as never, tasks);
    expect(prog.status).toBe('in_progress');
  });

  it('uses generation tasks only for expectedQuestions fallback', () => {
    const dataset = {
      _id: 'ds1',
      name: 'ds',
      metadata: {},
      questions: [],
    };
    const tasks: Task[] = [
      task('c', 'completed', 'chunk'),
      task('g1', 'pending', 'gate'),
      task('q1', 'pending', 'generation'),
      task('q2', 'pending', 'generation'),
    ];
    const prog = computeDatasetProgress(dataset as never, dataset as never, tasks);
    expect(prog.expectedQuestions).toBe(2);
  });
});
