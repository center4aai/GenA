import type { ChunkingMethod, DocumentKind } from '@/shared/types/documents';

/** Chunking methods selectable on the create page (mirrors chunker CHUNKING_METHODS). */
export const CHUNKING_METHODS: readonly ChunkingMethod[] = ['rules', 'layout', 'semantic', 'llm'] as const;

export const DEFAULT_CHUNKING_METHOD: ChunkingMethod = 'rules';

export const CHUNKING_METHOD_KEYS: Record<ChunkingMethod, string> = {
  rules: 'create:methodRules',
  layout: 'create:methodLayout',
  semantic: 'create:methodSemantic',
  llm: 'create:methodLlm',
};

export const CHUNKING_METHOD_HINT_KEYS: Record<ChunkingMethod, string> = {
  rules: 'create:methodHintRules',
  layout: 'create:methodHintLayout',
  semantic: 'create:methodHintSemantic',
  llm: 'create:methodHintLlm',
};

/**
 * Method preselected for a document kind. Textbooks and books default to
 * semantic: for PDF it builds on the layout extraction (insets, captions and
 * exercises kept apart) and adds topic boundaries by embeddings; other files
 * get the same boundaries on top of rules. Layout alone stays selectable by hand.
 */
export function defaultChunkingMethodFor(kind: DocumentKind): ChunkingMethod {
  return kind === 'textbook' ? 'semantic' : DEFAULT_CHUNKING_METHOD;
}

/** Legal documents are split by articles and items: only the rule-based method applies. */
export function chunkingMethodsForKind(kind: DocumentKind): readonly ChunkingMethod[] {
  return kind === 'normative' ? ['rules'] : CHUNKING_METHODS;
}

/** The method the backend will actually be asked for, given the document kind. */
export function effectiveChunkingMethod(kind: DocumentKind, method: ChunkingMethod): ChunkingMethod {
  return chunkingMethodsForKind(kind).includes(method) ? method : DEFAULT_CHUNKING_METHOD;
}

export function isChunkingMethod(value: unknown): value is ChunkingMethod {
  return typeof value === 'string' && (CHUNKING_METHODS as readonly string[]).includes(value);
}
