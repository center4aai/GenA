import type { ChunkDetailed, ChunkRecord, ChunkResponse, GateResult } from '../types/documents';
import { evaluateGatePass } from './gateDecision';

export interface ExtractedChunk {
  idx: number;
  text: string;
  fragmentData?: Record<string, unknown> | null;
  hierarchyContext?: Record<string, unknown>;
  gateResult?: unknown;
  gatePassed?: boolean;
  typesValid?: string[];
}

/** Convert stored chunk records to chunks_detailed format (bot.py existing dataset load). */
export function storedChunksToDetailed(stored: ChunkRecord[]): ChunkDetailed[] {
  return stored.map((sc) => {
    const fd = sc.fragment_data ?? {};
    const ct = sc.chunk_text ?? '';
    const combined = (fd.combined_text as string | undefined) ?? ct;
    const hierarchy =
      (sc.hierarchy_context as Record<string, unknown> | undefined)
      ?? (fd.hierarchy_context as Record<string, unknown> | undefined)
      ?? {};
    return {
      fragment_id: `stored_chunk_${sc.chunk_index}`,
      fragment_data: {
        ...fd,
        combined_text: combined,
        content: (fd.content as string | undefined) ?? ct,
        title: (fd.title as string | undefined) ?? '',
        hierarchy_context: hierarchy,
      },
      hierarchy_context: hierarchy,
      _chunk_index: sc.chunk_index,
      _gate_result: sc.gate_result,
      _gate_passed: sc.gate_passed ?? true,
      _question_types_valid: sc.question_types_valid,
    };
  });
}

/** Block types the chunker labels beyond plain body text. */
const LABELLED_BLOCK_TYPES = new Set(['inset', 'caption', 'glossary_entry', 'list']);

/** Chunker block type worth showing (body is the default and is not labelled). */
export function chunkBlockType(ch: Pick<ChunkDetailed, 'fragment_data'>): string | null {
  const bt = ch.fragment_data?.block_type;
  return typeof bt === 'string' && LABELLED_BLOCK_TYPES.has(bt) ? bt : null;
}

/** [page_start, page_end] from the chunker, or null when pages are unknown. */
export function chunkPageRange(
  ch: Pick<ChunkDetailed, 'fragment_data'>,
): [number, number] | null {
  const fd = ch.fragment_data ?? {};
  const start = typeof fd.page_start === 'number' ? fd.page_start : null;
  const end = typeof fd.page_end === 'number' ? fd.page_end : null;
  const from = start ?? end;
  if (from == null) return null;
  return [from, end ?? from];
}

export function buildChunkResponseFromStored(stored: ChunkRecord[]): ChunkResponse {
  const detailed = storedChunksToDetailed(stored);
  const texts = detailed.map(
    (ch) => ch.fragment_data?.combined_text ?? ch.fragment_data?.content ?? '',
  );
  return {
    num_chunks: detailed.length,
    chunks: texts,
    chunks_detailed: detailed,
    chunking_method: 'stored',
  };
}

/** Extract text and metadata from chunk items for generation. */
export function extractChunks(
  chunksDetailed: ChunkDetailed[] | string[] | undefined,
  chunksText: string[] | undefined,
): ExtractedChunk[] {
  const source = chunksDetailed?.length ? chunksDetailed : chunksText ?? [];
  const extracted: ExtractedChunk[] = [];

  source.forEach((chunk, index) => {
    const idx =
      typeof chunk === 'object' && chunk !== null
        ? (chunk as ChunkDetailed)._chunk_index
          ?? (() => {
            const m = (chunk as ChunkDetailed).fragment_id?.match(/_chunk_(\d+)$/);
            return m ? Number(m[1]) : undefined;
          })()
          ?? index + 1
        : index + 1;
    if (typeof chunk === 'string') {
      const text = chunk.trim();
      if (text.length >= 10) {
        extracted.push({ idx, text, fragmentData: null });
      }
      return;
    }

    const fd = chunk.fragment_data ?? {};
    const text =
      fd.combined_text ??
      fd.content ??
      fd.title ??
      '';
    const chunkText = String(text).trim();
    if (chunkText.length < 10) return;

    const hierarchyContext =
      chunk.hierarchy_context
      ?? (fd.hierarchy_context as Record<string, unknown> | undefined)
      ?? undefined;

    extracted.push({
      idx,
      text: chunkText,
      fragmentData: {
        ...fd,
        ...(hierarchyContext ? { hierarchy_context: hierarchyContext } : {}),
      },
      hierarchyContext,
      gateResult: chunk._gate_result,
      gatePassed: chunk._gate_passed ?? (chunk._gate_result as { passed?: boolean } | undefined)?.passed ?? true,
      typesValid: chunk._question_types_valid,
    });
  });

  return extracted;
}

/** Types already gate-validated on a stored chunk (bot.py _types_valid parity). */
export function storedQuestionTypes(ec: ExtractedChunk, questionTypes: string[]): string[] {
  if (ec.typesValid != null) return ec.typesValid;
  if (ec.gatePassed === false) return [];
  if (ec.gateResult) {
    return evaluateGatePass(ec.gateResult as GateResult, questionTypes).typesOk;
  }
  return [...questionTypes];
}

export function questionTypesToGenerate(
  ec: ExtractedChunk,
  questionTypes: string[],
  usingExisting: boolean,
): string[] {
  if (!usingExisting) return [...questionTypes];
  return questionTypes.filter((qt) => storedQuestionTypes(ec, questionTypes).includes(qt));
}

export function chunkPreviewText(chunk: ChunkDetailed | string): string {
  if (typeof chunk === 'string') return chunk;
  const fd = chunk.fragment_data ?? {};
  return fd.combined_text ?? fd.content ?? String(chunk);
}
