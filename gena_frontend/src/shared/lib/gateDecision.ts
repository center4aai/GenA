import type { GateResult } from '../types/documents';

function gateScore(value: unknown): number {
  if (Array.isArray(value) && value.length) {
    value = value[0];
  }
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'number') return value === 1 ? 1 : 0;
  if (typeof value === 'string') {
    const s = value.trim();
    if (s === '1' || s.toLowerCase() === 'true') return 1;
    if (s === '0' || s.toLowerCase() === 'false') return 0;
  }
  return 0;
}

/** Evaluate gate pass for a chunk (bot.py gate logic). */
export function evaluateGatePass(
  gateResult: GateResult,
  questionTypes: string[],
): { passed: boolean; typesOk: string[] } {
  const c1 = gateScore(gateResult.c1_chunk_informative);
  const c2 = gateScore(gateResult.c2_chunk_reference_clarity);
  const c3 = gateScore(gateResult.c3_chunk_multi_suitability);

  if (c1 === 0 || c2 === 0) {
    return { passed: false, typesOk: [] };
  }

  const typesOk = questionTypes.filter((qt) => qt !== 'multi' || c3 === 1);
  return { passed: typesOk.length > 0, typesOk };
}

/** Build disabled gate result for ablation modes. */
export function disabledGateResult(): GateResult {
  return {
    passed: true,
    rejection_reason: 'gate_disabled_by_pipeline_mode',
  };
}

export function gateRejectionSummary(gateResults: Record<number, GateResult>): string {
  const counts: Record<string, number> = {};
  for (const gr of Object.values(gateResults)) {
    if (!gr.passed) {
      const reason = gr.rejection_reason ?? 'unknown';
      counts[reason] = (counts[reason] ?? 0) + 1;
    }
  }
  return Object.entries(counts)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([reason, count]) => `${reason}: ${count}`)
    .join(', ');
}
