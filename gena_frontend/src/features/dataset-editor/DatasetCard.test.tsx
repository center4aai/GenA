import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import i18n from '@/shared/i18n';
import type { QuestionStats } from '@/shared/lib/questionStats';
import type { DatasetDetail, DatasetMetadata } from '@/shared/types/documents';
import { DatasetCard } from './DatasetCard';

const t = (key: string, opts?: Record<string, unknown>) => String(i18n.t(key, opts as never));

const stats: QuestionStats = {
  total: 3,
  types: { one: 1, multi: 1, open: 1 },
  sensitivity: { '1': 1, '2': 1, '3': 1 },
  difficulty: { '1': 1, '2': 1, '3': 1 },
  withVerdict: 3,
  passed: 2,
  passRate: 66.7,
  excluded: 0,
};

function dataset(metadata: DatasetMetadata): DatasetDetail {
  return { _id: 'd1', name: 'Set', source_document: 'book.pdf', metadata, questions: [] } as DatasetDetail;
}

function renderCard(metadata: DatasetMetadata) {
  render(
    <DatasetCard
      dataset={dataset(metadata)}
      versions={[{ version: 1, created_at: '2026-10-12T10:00:00Z' }]}
      version={1}
      onVersionChange={vi.fn()}
      stats={stats}
      toolbar={{
        onShowExcluded: vi.fn(),
        shuffle: false,
        onShuffleChange: vi.fn(),
        onExcel: vi.fn(),
        onGift: vi.fn(),
        onPrint: vi.fn(),
        onExtended: vi.fn(),
        message: null,
      }}
    />,
  );
}

describe('DatasetCard chunking facts', () => {
  it('shows the engine and the llm window statistics next to the method', () => {
    renderCard({
      status: 'completed',
      document_kind: 'textbook',
      chunking_method: 'llm',
      chunking_engine: 'llm_segmentation',
      chunking_stats: {
        paragraph_source: 'layout',
        llm_segmentation: { windows: 95, llm_ok: 90, fallback: 5, salvaged_truncated: 2 },
      },
    });
    expect(screen.getByText(t('create:methodLlm'))).toBeTruthy();
    expect(screen.getByText(t('create:chunkingEngine', { engine: 'llm_segmentation' }))).toBeTruthy();
    expect(
      screen.getByText(
        t('create:llmSegStats', { windows: 95, ok: 90, fallback: 5, truncated: 2, source: t('create:paragraphSource.layout') }),
      ),
    ).toBeTruthy();
  });

  it('marks a fallback and keeps the reason in the tooltip', () => {
    renderCard({
      status: 'completed',
      document_kind: 'textbook',
      chunking_method: 'llm',
      chunking_engine: 'llm_segmentation',
      chunking_fallback: 'llm: 3/10 windows by rules (deadline: 3)',
    });
    const badge = screen.getByText(t('create:methodFellBack'));
    expect(badge.getAttribute('title')).toBe('llm: 3/10 windows by rules (deadline: 3)');
    expect(screen.queryByText(/окон:|windows:/)).toBeNull();
  });

  it('stays quiet for the rules method without diagnostics', () => {
    renderCard({ status: 'completed', document_kind: 'normative', chunking_method: 'rules' });
    expect(screen.getByText(t('create:methodRules'))).toBeTruthy();
    expect(screen.queryByText(/движок|engine/)).toBeNull();
  });
});
