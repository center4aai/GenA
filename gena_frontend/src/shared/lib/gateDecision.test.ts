import { describe, expect, it } from 'vitest';
import {
  disabledGateResult,
  evaluateGatePass,
  gateRejectionSummary,
} from './gateDecision';
import type { GateResult } from '../types/documents';

const ALL_TYPES = ['one', 'multi', 'open'];

function gate(overrides: GateResult): GateResult {
  return overrides;
}

describe('evaluateGatePass', () => {
  it('passes when all criteria are met', () => {
    const result = evaluateGatePass(
      gate({
        c1_chunk_informative: [1],
        c2_chunk_reference_clarity: [1],
        c3_chunk_multi_suitability: [1],
      }),
      ALL_TYPES,
    );
    expect(result).toEqual({ passed: true, typesOk: ALL_TYPES });
  });

  it('fails when c1 is zero', () => {
    const result = evaluateGatePass(
      gate({
        c1_chunk_informative: [0],
        c2_chunk_reference_clarity: [1],
        c3_chunk_multi_suitability: [1],
      }),
      ALL_TYPES,
    );
    expect(result).toEqual({ passed: false, typesOk: [] });
  });

  it('fails when c2 is zero', () => {
    const result = evaluateGatePass(
      gate({
        c1_chunk_informative: [1],
        c2_chunk_reference_clarity: [0],
        c3_chunk_multi_suitability: [1],
      }),
      ALL_TYPES,
    );
    expect(result).toEqual({ passed: false, typesOk: [] });
  });

  it('drops multi type only when c3 is zero', () => {
    const result = evaluateGatePass(
      gate({
        c1_chunk_informative: [1],
        c2_chunk_reference_clarity: [1],
        c3_chunk_multi_suitability: [0],
      }),
      ALL_TYPES,
    );
    expect(result).toEqual({ passed: true, typesOk: ['one', 'open'] });
  });

  it('fails when only multi is requested and c3 is zero', () => {
    const result = evaluateGatePass(
      gate({
        c1_chunk_informative: [1],
        c2_chunk_reference_clarity: [1],
        c3_chunk_multi_suitability: [0],
      }),
      ['multi'],
    );
    expect(result).toEqual({ passed: false, typesOk: [] });
  });

  it('passes all types when gate is disabled by pipeline mode', () => {
    const result = evaluateGatePass(
      gate({ rejection_reason: 'gate_disabled_by_pipeline_mode', passed: false }),
      ALL_TYPES,
    );
    expect(result).toEqual({ passed: true, typesOk: ALL_TYPES });
  });

  it('trusts stored synthetic pass without criteria fields', () => {
    const result = evaluateGatePass(gate({ passed: true }), ALL_TYPES);
    expect(result).toEqual({ passed: true, typesOk: ALL_TYPES });
  });
});

describe('gateRejectionSummary', () => {
  it('aggregates rejection reasons for failed chunks', () => {
    const results: Record<number, GateResult> = {
      0: gate({
        passed: false,
        rejection_reason: 'low_informativeness',
        c1_chunk_informative: [0],
        c2_chunk_reference_clarity: [1],
      }),
      1: gate({
        passed: false,
        rejection_reason: 'low_informativeness',
        c1_chunk_informative: [0],
        c2_chunk_reference_clarity: [1],
      }),
      2: gate({
        passed: false,
        rejection_reason: 'unclear_reference',
        c1_chunk_informative: [1],
        c2_chunk_reference_clarity: [0],
      }),
      3: gate({
        passed: true,
        c1_chunk_informative: [1],
        c2_chunk_reference_clarity: [1],
        c3_chunk_multi_suitability: [1],
      }),
    };
    expect(gateRejectionSummary(results, ALL_TYPES)).toBe(
      'low_informativeness: 2, unclear_reference: 1',
    );
  });

  it('uses passed flag when question types omitted', () => {
    const results: Record<number, GateResult> = {
      0: { passed: false, rejection_reason: 'manual_skip' },
      1: { passed: true },
    };
    expect(gateRejectionSummary(results)).toBe('manual_skip: 1');
  });
});

describe('disabledGateResult', () => {
  it('returns a passing result with pipeline disable reason', () => {
    expect(disabledGateResult()).toEqual({
      passed: true,
      rejection_reason: 'gate_disabled_by_pipeline_mode',
    });
  });
});
