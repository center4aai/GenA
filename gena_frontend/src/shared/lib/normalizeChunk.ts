import type { ChunkDetailed, ChunkRecord, ChunkResponse } from '../types/documents';

export interface ExtractedChunk {
  idx: number;
  text: string;
  fragmentData?: Record<string, unknown> | null;
  gateResult?: unknown;
  typesValid?: string[];
}

/** Normalize chunker API response into unified structure. */
export function normalizeChunkResponse(
  data: ChunkResponse,
  fallbackName?: string,
): { data: ChunkResponse; documentName: string } {
  let documentName = fallbackName ?? '';
  if (data.document_type && typeof data.document_type === 'object') {
    documentName = data.document_type.document_name ?? documentName;
  }
  return { data, documentName };
}

/** Convert stored chunk records to chunks_detailed format (bot.py existing dataset load). */
export function storedChunksToDetailed(stored: ChunkRecord[]): ChunkDetailed[] {
  return stored.map((sc) => {
    const fd = sc.fragment_data ?? {};
    const ct = sc.chunk_text ?? '';
    const combined = (fd.combined_text as string | undefined) ?? ct;
    return {
      fragment_id: `stored_chunk_${sc.chunk_index}`,
      fragment_data: {
        ...fd,
        combined_text: combined,
        content: (fd.content as string | undefined) ?? ct,
        title: (fd.title as string | undefined) ?? '',
      },
      hierarchy_context: (fd.hierarchy_context as Record<string, unknown>) ?? {},
      _gate_result: sc.gate_result,
      _gate_passed: sc.gate_passed ?? true,
      _question_types_valid: sc.question_types_valid,
    };
  });
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
    const idx = index + 1;
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

    extracted.push({
      idx,
      text: chunkText,
      fragmentData: fd as Record<string, unknown>,
      gateResult: chunk._gate_result,
      typesValid: chunk._question_types_valid,
    });
  });

  return extracted;
}

export function chunkPreviewText(chunk: ChunkDetailed | string): string {
  if (typeof chunk === 'string') return chunk;
  const fd = chunk.fragment_data ?? {};
  return fd.combined_text ?? fd.content ?? String(chunk);
}
