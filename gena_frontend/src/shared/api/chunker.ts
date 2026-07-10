import { chunkerFetch } from './http';
import type { ChunkResponse } from '../types/documents';

export async function chunkDocument(file: File): Promise<ChunkResponse> {
  const formData = new FormData();
  formData.append('file', file, file.name);

  const response = await chunkerFetch('/chunk/', {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`Chunker error ${response.status}: ${text}`);
  }

  return response.json() as Promise<ChunkResponse>;
}
