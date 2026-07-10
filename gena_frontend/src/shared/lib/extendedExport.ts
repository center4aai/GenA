import { normOptions } from './optionsFormat';
import { parseValidationScore } from './progress';
import type { ChunkRecord, Question } from '../types/documents';

const VALIDATION_MAX_POINTS: Record<string, number> = {
  open: 16.0,
  one: 20.5,
  multi: 20.5,
};

// Exact order of the extended "All Pipeline" template (mirrors
// dataset_editor._EXTENDED_EXPORT_COLUMNS).
export const EXTENDED_EXPORT_COLUMNS = [
  'chunk_id',
  'gate_passed',
  'gate_rejection_reason',
  'c1_chunk_informative',
  'c2_chunk_reference_clarity',
  'c3_chunk_multi_suitability',
  'c1_confidence',
  'c2_confidence',
  'c3_confidence',
  'c1_reasoning',
  'c2_reasoning',
  'c3_reasoning',
  'gate_confidence',
  'gate_justifications',
  'question_id',
  'question_type',
  'task',
  'options',
  'correct_answer',
  'sensitivity_level',
  'difficulty_level',
  'validation_passed',
  'validation_score',
  'validation_threshold',
  'validation_max',
  'validation_details',
  'validation_justifications',
  'retry_count',
  'source_chunk',
] as const;

function gateScore(value: unknown): number | null {
  if (Array.isArray(value) && value.length) value = value[0];
  if (typeof value === 'boolean') return Number(value);
  if (typeof value === 'number') return value === 1 ? 1 : 0;
  if (typeof value === 'string') {
    if (['1', 'true', 'True'].includes(value.trim())) return 1;
    if (['0', 'false', 'False'].includes(value.trim())) return 0;
  }
  return null;
}

function gateColumnsForChunk(chunk?: ChunkRecord) {
  if (!chunk) {
    return {
      gate_passed: null,
      gate_rejection_reason: null,
      c1_chunk_informative: null,
      c2_chunk_reference_clarity: null,
      c3_chunk_multi_suitability: null,
      c1_confidence: null,
      c2_confidence: null,
      c3_confidence: null,
      c1_reasoning: null,
      c2_reasoning: null,
      c3_reasoning: null,
      gate_confidence: null,
      gate_justifications: null,
      source_chunk: null,
    };
  }
  const gateResult = chunk.gate_result ?? {};
  const gatePassed = chunk.gate_passed ?? gateResult.passed;
  const confidence = {
    c1_chunk_informative: gateResult.c1_confidence ?? null,
    c2_chunk_reference_clarity: gateResult.c2_confidence ?? null,
    c3_chunk_multi_suitability: gateResult.c3_confidence ?? null,
  };
  const justifications = {
    c1_chunk_informative: gateResult.c1_reasoning ?? null,
    c2_chunk_reference_clarity: gateResult.c2_reasoning ?? null,
    c3_chunk_multi_suitability: gateResult.c3_reasoning ?? null,
  };
  return {
    gate_passed: gateScore(gatePassed),
    gate_rejection_reason: gateResult.rejection_reason ?? null,
    c1_chunk_informative: gateScore(gateResult.c1_chunk_informative),
    c2_chunk_reference_clarity: gateScore(gateResult.c2_chunk_reference_clarity),
    c3_chunk_multi_suitability: gateScore(gateResult.c3_chunk_multi_suitability),
    c1_confidence: gateResult.c1_confidence ?? null,
    c2_confidence: gateResult.c2_confidence ?? null,
    c3_confidence: gateResult.c3_confidence ?? null,
    c1_reasoning: gateResult.c1_reasoning ?? null,
    c2_reasoning: gateResult.c2_reasoning ?? null,
    c3_reasoning: gateResult.c3_reasoning ?? null,
    gate_confidence: confidence,
    gate_justifications: justifications,
    source_chunk: chunk.chunk_text ?? null,
  };
}

export function validationScorePair(q: Question): [number | null, number | null] {
  const [score, maxTotal] = parseValidationScore(q.validation_score);
  if (maxTotal != null) return [score, maxTotal];
  const qtype = (q.question_type ?? '').trim().toLowerCase();
  return [score, VALIDATION_MAX_POINTS[qtype] ?? null];
}

function chunkSortKey(v: unknown): [number, number | string] {
  const n = Number(v);
  if (!Number.isNaN(n) && v != null && v !== '') return [0, n];
  return [1, v != null ? String(v) : ''];
}

export function buildExtendedExportRows(questions: Question[], chunks: ChunkRecord[]) {
  const chunksById: Record<string | number, ChunkRecord> = {};
  for (const c of chunks) {
    if (c.chunk_index != null) {
      chunksById[c.chunk_index] = c;
      chunksById[String(c.chunk_index)] = c;
    }
  }

  const rows: Record<string, unknown>[] = [];
  const seenChunkIds = new Set<string | number>();

  for (const q of questions) {
    const chunkId = q.chunk_id;
    if (chunkId != null) {
      seenChunkIds.add(chunkId);
      seenChunkIds.add(String(chunkId));
    }
    const chunk = chunkId != null ? chunksById[chunkId] ?? chunksById[String(chunkId)] : undefined;
    const gateCols = gateColumnsForChunk(chunk);
    const [score, maxTotal] = validationScorePair(q);

    rows.push({
      chunk_id: chunkId,
      ...gateCols,
      question_id: q.question_id,
      question_type: q.question_type,
      task: q.task,
      options: normOptions(q.options),
      correct_answer: q.correct_answer,
      sensitivity_level: q.sensitivity_level ?? q.provocativeness,
      difficulty_level: q.difficulty_level ?? q.difficulty,
      validation_passed: q.validation_passed,
      validation_score: score ?? q.validation_score,
      validation_threshold: q.validation_threshold,
      validation_max: maxTotal,
      validation_details: q.validation_details,
      validation_justifications: q.validation_justifications,
      retry_count: q.retry_count,
      source_chunk: q.source_chunk ?? gateCols.source_chunk,
    });
  }

  for (const [cid, chunk] of Object.entries(chunksById)) {
    if (seenChunkIds.has(cid) || seenChunkIds.has(Number(cid))) continue;
    seenChunkIds.add(cid);
    const gateCols = gateColumnsForChunk(chunk);
    rows.push({
      chunk_id: chunk.chunk_index,
      ...gateCols,
      question_id: null,
      question_type: null,
      task: null,
      options: null,
      correct_answer: null,
      sensitivity_level: null,
      difficulty_level: null,
      validation_passed: null,
      validation_score: null,
      validation_threshold: null,
      validation_max: null,
      validation_details: null,
      validation_justifications: null,
      retry_count: null,
    });
  }

  rows.sort((a, b) => {
    const [ka0, ka1] = chunkSortKey(a.chunk_id);
    const [kb0, kb1] = chunkSortKey(b.chunk_id);
    if (ka0 !== kb0) return ka0 - kb0;
    if (ka1 < kb1) return -1;
    if (ka1 > kb1) return 1;
    return String(a.question_id ?? '').localeCompare(String(b.question_id ?? ''));
  });

  // Ensure every row has the full column set in the exact order.
  return rows.map((r) => {
    const ordered: Record<string, unknown> = {};
    for (const col of EXTENDED_EXPORT_COLUMNS) ordered[col] = r[col] ?? null;
    return ordered;
  });
}

export function buildResultsExportRows(questions: Question[]) {
  const coreOrder = [
    'chunk_id',
    'question_id',
    'question_type',
    'task',
    'options',
    'correct_answer',
    'sensitivity_level',
    'difficulty_level',
    'validation_passed',
    'validation_score',
    'validation_threshold',
    'validation_max',
    'validation_details',
    'validation_justifications',
    'retry_count',
    'source_chunk',
  ];
  return questions.map((q) => {
    const [score, maxTotal] = validationScorePair(q);
    const row: Record<string, unknown> = {
      chunk_id: q.chunk_id,
      question_id: q.question_id,
      question_type: q.question_type,
      task: q.task,
      options: normOptions(q.options),
      correct_answer: q.correct_answer,
      sensitivity_level: q.sensitivity_level ?? q.provocativeness,
      difficulty_level: q.difficulty_level ?? q.difficulty,
      validation_passed: q.validation_passed,
      validation_score: score ?? q.validation_score,
      validation_threshold: q.validation_threshold,
      validation_max: maxTotal,
      validation_details: q.validation_details,
      validation_justifications: q.validation_justifications,
      retry_count: q.retry_count,
      source_chunk: q.source_chunk,
    };
    const ordered: Record<string, unknown> = {};
    for (const col of coreOrder) ordered[col] = row[col] ?? null;
    return ordered;
  });
}

export function downloadCsv(filename: string, rows: Record<string, unknown>[]) {
  if (!rows.length) return;
  const headers = Object.keys(rows[0]);
  const lines = [
    headers.join(','),
    ...rows.map((r) =>
      headers.map((h) => `"${String(r[h] ?? '').replace(/"/g, '""')}"`).join(','),
    ),
  ];
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
