import { describe, expect, it } from 'vitest';
import {
  CHUNKING_METHODS,
  chunkingMethodsForKind,
  defaultChunkingMethodFor,
  effectiveChunkingMethod,
  isChunkingMethod,
} from './chunkingMethods';

describe('chunking methods', () => {
  it('offers every method for prose kinds and only rules for legal documents', () => {
    expect(chunkingMethodsForKind('textbook')).toEqual(CHUNKING_METHODS);
    expect(chunkingMethodsForKind('encyclopedia')).toEqual(CHUNKING_METHODS);
    expect(chunkingMethodsForKind('normative')).toEqual(['rules']);
  });

  it('falls back to rules when the method does not apply to the kind', () => {
    expect(effectiveChunkingMethod('normative', 'llm')).toBe('rules');
    expect(effectiveChunkingMethod('textbook', 'llm')).toBe('llm');
  });

  it('recognises stored method values', () => {
    expect(isChunkingMethod('layout')).toBe(true);
    expect(isChunkingMethod('hierarchical_outline')).toBe(false);
    expect(isChunkingMethod(undefined)).toBe(false);
  });

  it('preselects semantic for textbooks and rules for everything else', () => {
    expect(defaultChunkingMethodFor('textbook')).toBe('semantic');
    expect(defaultChunkingMethodFor('encyclopedia')).toBe('rules');
    expect(defaultChunkingMethodFor('normative')).toBe('rules');
  });
});
