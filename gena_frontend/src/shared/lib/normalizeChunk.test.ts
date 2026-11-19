import { describe, expect, it } from 'vitest';
import { buildChunkResponseFromStored, chunkPreviewText, extractChunks, questionTypesToGenerate, storedChunksToDetailed, storedQuestionTypes, chunkBlockType, chunkPageRange } from './normalizeChunk';
import type { ChunkDetailed, ChunkRecord } from '../types/documents';

describe('normalizeChunk', () => {
  it('storedChunksToDetailed defaults gate_passed to true', () => {
    const stored: ChunkRecord[] = [
      {
        chunk_index: 1,
        chunk_text: 'Sample chunk text long enough',
        fragment_data: { content: 'inner' },
      },
    ];
    const detailed = storedChunksToDetailed(stored);
    expect(detailed[0]._gate_passed).toBe(true);
    expect(detailed[0].fragment_data?.combined_text).toContain('Sample chunk');
  });

  it('buildChunkResponseFromStored produces chunks_detailed', () => {
    const stored: ChunkRecord[] = [
      { chunk_index: 1, chunk_text: 'abcdefghij', fragment_data: {} },
    ];
    const resp = buildChunkResponseFromStored(stored);
    expect(resp.num_chunks).toBe(1);
    expect(resp.chunks_detailed?.length).toBe(1);
    expect(resp.chunking_method).toBe('stored');
  });

  it('extractChunks skips short string chunks', () => {
    const extracted = extractChunks(['short', 'long enough text here'], undefined);
    expect(extracted).toHaveLength(1);
    expect(extracted[0].idx).toBe(2);
  });

  it('storedChunksToDetailed preserves top-level hierarchy_context', () => {
    const stored: ChunkRecord[] = [
      {
        chunk_index: 1,
        chunk_text: 'Sample chunk text long enough',
        hierarchy_context: { section: '2' },
        fragment_data: { content: 'inner' },
      },
    ];
    const detailed = storedChunksToDetailed(stored);
    expect(detailed[0].hierarchy_context).toEqual({ section: '2' });
  });

  it('extractChunks carries hierarchy_context for save roundtrip', () => {
    const detailed: ChunkDetailed[] = [
      {
        fragment_id: 'f1',
        fragment_data: {
          combined_text: 'This is a sufficiently long chunk body',
          title: 'Art. 1',
        },
        hierarchy_context: { section: '1' },
      },
    ];
    const extracted = extractChunks(detailed, undefined);
    expect(extracted[0].hierarchyContext).toEqual({ section: '1' });
  });

  it('extractChunks reads detailed fragment_data fields', () => {
    const detailed: ChunkDetailed[] = [
      {
        fragment_id: 'f1',
        fragment_data: {
          combined_text: 'This is a sufficiently long chunk body',
          title: 'Art. 1',
        },
        hierarchy_context: { section: '1' },
        _gate_passed: false,
        _question_types_valid: ['one'],
      },
    ];
    const extracted = extractChunks(detailed, undefined);
    expect(extracted[0].text).toContain('sufficiently long');
    expect(extracted[0].typesValid).toEqual(['one']);
  });

  it('extractChunks uses stored chunk_index not array position', () => {
    const detailed: ChunkDetailed[] = [
      {
        fragment_id: 'stored_chunk_3',
        fragment_data: { combined_text: 'This is a sufficiently long chunk body' },
        hierarchy_context: {},
        _chunk_index: 3,
      },
    ];
    const extracted = extractChunks(detailed, undefined);
    expect(extracted[0].idx).toBe(3);
  });

  it('extractChunks parses chunk index from fragment_id', () => {
    const detailed: ChunkDetailed[] = [
      {
        fragment_id: 'doc_chunk_7',
        fragment_data: { combined_text: 'This is a sufficiently long chunk body' },
        hierarchy_context: {},
      },
    ];
    const extracted = extractChunks(detailed, undefined);
    expect(extracted[0].idx).toBe(7);
  });

  it('storedQuestionTypes skips gate-failed chunks without typesValid', () => {
    const ec = {
      idx: 1,
      text: 'long enough chunk',
      gatePassed: false,
    };
    expect(storedQuestionTypes(ec, ['one', 'multi'])).toEqual([]);
    expect(questionTypesToGenerate(ec, ['one', 'multi'], true)).toEqual([]);
  });

  it('storedQuestionTypes falls back to all types when gate passed', () => {
    const ec = { idx: 1, text: 'long enough chunk', gatePassed: true };
    expect(storedQuestionTypes(ec, ['one', 'multi'])).toEqual(['one', 'multi']);
  });

  it('storedQuestionTypes filters multi from gate result when typesValid missing', () => {
    const ec = {
      idx: 1,
      text: 'long enough chunk',
      gatePassed: true,
      gateResult: {
        passed: true,
        c1_chunk_informative: [1],
        c2_chunk_reference_clarity: [1],
        c3_chunk_multi_suitability: [0],
      },
    };
    expect(storedQuestionTypes(ec, ['one', 'multi'])).toEqual(['one']);
  });

  it('chunkPreviewText prefers combined_text', () => {
    const chunk: ChunkDetailed = {
      fragment_id: 'f1',
      fragment_data: { combined_text: 'combined', content: 'content' },
      hierarchy_context: {},
    };
    expect(chunkPreviewText(chunk)).toBe('combined');
    expect(chunkPreviewText('plain')).toBe('plain');
  });
});

describe('chunk block metadata helpers', () => {
  it('reports labelled block types and ignores body/unknown', () => {
    expect(chunkBlockType({ fragment_data: { block_type: 'inset' } })).toBe('inset');
    expect(chunkBlockType({ fragment_data: { block_type: 'glossary_entry' } })).toBe('glossary_entry');
    expect(chunkBlockType({ fragment_data: { block_type: 'body' } })).toBeNull();
    expect(chunkBlockType({ fragment_data: {} })).toBeNull();
    expect(chunkBlockType({})).toBeNull();
  });

  it('derives a page range from page_start / page_end', () => {
    expect(chunkPageRange({ fragment_data: { page_start: 12, page_end: 13 } })).toEqual([12, 13]);
    expect(chunkPageRange({ fragment_data: { page_start: 12 } })).toEqual([12, 12]);
    expect(chunkPageRange({ fragment_data: { page_end: 9 } })).toEqual([9, 9]);
    expect(chunkPageRange({ fragment_data: { page_start: null } })).toBeNull();
    expect(chunkPageRange({})).toBeNull();
  });
});
