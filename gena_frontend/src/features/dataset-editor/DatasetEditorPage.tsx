import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listDatasets,
  getDataset,
  listDatasetVersions,
  listDatasetChunks,
  updateDataset,
} from '@/shared/api/dataset';
import type { Question } from '@/shared/types/documents';
import { useAuth, isExpert } from '@/features/auth/authStore';
import {
  asOptionsText,
  coerceOptionsLike,
  normOptions,
} from '@/shared/lib/optionsFormat';
import {
  formatRetryLine,
  formatValidationBreakdown,
  parseValidationDetails,
  parseValidationJustifications,
} from '@/shared/lib/validation';
import { isValidationPassed } from '@/shared/lib/progress';
import { diffVersions } from '@/shared/lib/diffVersions';
import { buildExtendedExportRows, buildResultsExportRows } from '@/shared/lib/extendedExport';
import { downloadXlsx } from '@/shared/lib/xlsx';
import { questionTypeLabel, levelLabel, toCategoryData, truncIso } from '@/shared/lib/labels';
import { PageHeader } from '@/shared/ui/PageHeader';
import { MetricCard } from '@/shared/ui/MetricCard';
import { BarChartRc } from '@/shared/ui/charts';
import { StatusBanner, LoadingState, EmptyState } from '@/shared/ui/StatusBanner';

const PAGE_SIZE = 10;

function ensureQids(datasetId: string, questions: Question[]): Question[] {
  return questions.map((q, idx) => ({
    ...q,
    question_id: q.question_id ?? `${datasetId}:${String(idx).padStart(5, '0')}`,
  }));
}

export function DatasetEditorPage() {
  const auth = useAuth();
  const expert = isExpert(auth.role);
  const queryClient = useQueryClient();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [compareV1, setCompareV1] = useState<number | null>(null);
  const [compareV2, setCompareV2] = useState<number | null>(null);
  const [showCompare, setShowCompare] = useState(false);
  const [page, setPage] = useState(1);
  const [edits, setEdits] = useState<Record<number, Partial<Question>>>({});

  const { data: datasetsRaw = [], isLoading: loadingList, isError: listError } = useQuery({
    queryKey: ['datasets'],
    queryFn: listDatasets,
  });

  const datasets = useMemo(
    () => [...datasetsRaw].sort((a, b) => String(b.created_at ?? '').localeCompare(String(a.created_at ?? ''))),
    [datasetsRaw],
  );

  const activeId = selectedId ?? datasets[0]?._id ?? null;

  const { data: versions = [] } = useQuery({
    queryKey: ['dataset-versions', activeId],
    queryFn: () => listDatasetVersions(activeId!),
    enabled: !!activeId,
  });

  const version = selectedVersion ?? versions[0]?.version ?? null;

  const { data: dataset, isLoading: loadingDataset } = useQuery({
    queryKey: ['dataset', activeId, version],
    queryFn: () => getDataset(activeId!, version ?? undefined),
    enabled: !!activeId && version != null,
  });

  const { data: chunks = [] } = useQuery({
    queryKey: ['dataset-chunks', activeId],
    queryFn: () => listDatasetChunks(activeId!, false),
    enabled: !!activeId,
  });

  const questions = useMemo(
    () => ensureQids(activeId ?? '', dataset?.questions ?? []),
    [activeId, dataset?.questions],
  );

  const numPages = Math.max(1, Math.ceil(questions.length / PAGE_SIZE));
  const start = (page - 1) * PAGE_SIZE;
  const pageQuestions = questions.slice(start, start + PAGE_SIZE);

  const passedRate = useMemo(() => {
    const flags = questions.map(isValidationPassed).filter((p) => p !== null) as boolean[];
    if (!flags.length) return '—';
    return `${((flags.filter(Boolean).length / flags.length) * 100).toFixed(1)}%`;
  }, [questions]);

  const typeData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of questions) {
      const t = questionTypeLabel(q.question_type);
      c[t] = (c[t] ?? 0) + 1;
    }
    return toCategoryData(c);
  }, [questions]);

  const provData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of questions) {
      const raw = q.sensitivity_level ?? q.provocativeness;
      if (raw == null || String(raw).trim() === '') continue;
      c[levelLabel(raw)] = (c[levelLabel(raw)] ?? 0) + 1;
    }
    return toCategoryData(c, ['Low', 'Medium', 'High']);
  }, [questions]);

  const scoreData = useMemo(() => {
    const c: Record<string, number> = {};
    for (const q of questions) {
      const raw = String(q.validation_score ?? '');
      const total = raw.split('/')[0]?.trim();
      if (total) c[total] = (c[total] ?? 0) + 1;
    }
    return Object.entries(c)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([label, value]) => ({ label, value }));
  }, [questions]);

  const editCount = Object.keys(edits).length;

  const { data: compareResult } = useQuery({
    queryKey: ['version-compare', activeId, compareV1, compareV2, showCompare],
    queryFn: async () => {
      const [ds1, ds2] = await Promise.all([
        getDataset(activeId!, compareV1!),
        getDataset(activeId!, compareV2!),
      ]);
      return diffVersions(ds1, ds2);
    },
    enabled: showCompare && !!activeId && compareV1 != null && compareV2 != null && compareV1 !== compareV2,
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const updated = questions.map((q, idx) => {
        const e = edits[idx];
        if (!e) {
          return {
            question_id: q.question_id,
            chunk_id: q.chunk_id,
            question_type: q.question_type,
            task: q.task,
            options: q.options,
            correct_answer: q.correct_answer,
            provocativeness: q.provocativeness,
            difficulty: q.difficulty,
            validation_passed: q.validation_passed,
            validation_score: q.validation_score,
            validation_threshold: q.validation_threshold,
            validation_details: q.validation_details,
            validation_justifications: q.validation_justifications,
            retry_count: q.retry_count,
            source_chunk: q.source_chunk,
          };
        }
        return {
          question_id: q.question_id,
          chunk_id: q.chunk_id ?? e.chunk_id,
          question_type: e.question_type ?? q.question_type,
          task: e.task ?? q.task,
          options: e.options != null ? coerceOptionsLike(q.options, e.options) : q.options,
          correct_answer: e.correct_answer ?? q.correct_answer,
          provocativeness: e.provocativeness ?? q.provocativeness,
          difficulty: e.difficulty !== undefined ? e.difficulty : q.difficulty,
          validation_passed: q.validation_passed,
          validation_score: q.validation_score,
          validation_threshold: q.validation_threshold,
          validation_details: q.validation_details,
          validation_justifications: q.validation_justifications,
          retry_count: q.retry_count,
          source_chunk: q.source_chunk,
        };
      });
      const metadata = {
        ...(dataset?.metadata ?? {}),
        edited_at: new Date().toISOString(),
        edited_by: auth.username ?? 'expert',
      };
      return updateDataset(activeId!, { questions: updated, metadata });
    },
    onSuccess: () => {
      setEdits({});
      queryClient.invalidateQueries({ queryKey: ['dataset', activeId] });
      queryClient.invalidateQueries({ queryKey: ['dataset-versions', activeId] });
    },
  });

  function updateEdit(globalIdx: number, patch: Partial<Question>) {
    setEdits((prev) => {
      const original = questions[globalIdx] ?? {};
      const merged = { ...prev[globalIdx], ...patch } as Partial<Question>;
      // Prune fields that were reverted back to their original values.
      const pruned: Partial<Question> = {};
      for (const [key, value] of Object.entries(merged)) {
        const orig = (original as Record<string, unknown>)[key];
        const origText = key === 'options' ? asOptionsText(orig) : orig == null ? '' : String(orig);
        const valText = key === 'options' && typeof value !== 'string' ? asOptionsText(value) : value == null ? '' : String(value);
        if (valText !== origText) (pruned as Record<string, unknown>)[key] = value;
      }
      const next = { ...prev };
      if (Object.keys(pruned).length) next[globalIdx] = pruned;
      else delete next[globalIdx];
      return next;
    });
  }

  async function exportResultsXlsx() {
    const rows = buildResultsExportRows(questions);
    await downloadXlsx(`${dataset?.name ?? 'dataset'}_v${version}.xlsx`, rows, 'Results');
  }

  async function exportPipelineXlsx() {
    const rows = buildExtendedExportRows(questions, chunks);
    await downloadXlsx(`${dataset?.name ?? 'dataset'}_v${version}_pipeline.xlsx`, rows, 'Pipeline');
  }

  if (loadingList) return <LoadingState label="Loading datasets…" />;
  if (listError) {
    return (
      <div>
        <PageHeader title="Results & Editor" subtitle="Could not load datasets. Check API connection and sign in." />
      </div>
    );
  }

  if (!datasets.length) {
    return (
      <div>
        <PageHeader title="Results & Editor" subtitle="Browse generated questions, edit inline, compare versions, and export." />
        <EmptyState title="No datasets yet" description="Generate a dataset using Data Preprocessing." />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Results & Editor"
        subtitle="Browse generated questions, edit inline, compare versions, and export."
      />

      <section className="space-y-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Available Datasets</h2>
        <div className="flex flex-col gap-3 sm:flex-row">
          <select
            className="input-field max-w-xl"
            value={activeId ?? ''}
            onChange={(e) => {
              setSelectedId(e.target.value);
              setSelectedVersion(null);
              setPage(1);
              setEdits({});
              setShowCompare(false);
            }}
          >
            {datasets.map((ds) => (
              <option key={ds._id} value={ds._id}>
                {ds.name} (v{ds.current_version ?? '?'}) — {ds._id.slice(0, 8)}
              </option>
            ))}
          </select>

          {versions.length > 0 && (
            <select
              className="input-field max-w-xs"
              value={version ?? ''}
              onChange={(e) => {
                setSelectedVersion(parseInt(e.target.value, 10));
                setPage(1);
                setEdits({});
              }}
            >
              {versions.map((v) => (
                <option key={v.version} value={v.version}>
                  Version {v.version} ({v.created_at.slice(0, 10)})
                </option>
              ))}
            </select>
          )}
        </div>
      </section>

      {loadingDataset && <div className="mt-6"><LoadingState label="Loading dataset…" /></div>}

      {dataset && (
        <>
          <div className="mt-6 card">
            <h3 className="text-lg font-semibold">{dataset.name}</h3>
            <p className="mt-1 text-sm text-slate-600">{dataset.description || 'No description'}</p>
            <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
              <div><dt className="text-slate-500">Source document</dt><dd>{dataset.source_document ?? '—'}</dd></div>
              <div><dt className="text-slate-500">Current version</dt><dd>{dataset.current_version ?? '?'}</dd></div>
              <div><dt className="text-slate-500">Viewing version</dt><dd>{dataset.requested_version ?? version}</dd></div>
              <div><dt className="text-slate-500">Created</dt><dd>{truncIso(dataset.created_at)}</dd></div>
              <div><dt className="text-slate-500">Last updated</dt><dd>{truncIso(dataset.updated_at ?? dataset.metadata?.last_updated)}</dd></div>
              <div><dt className="text-slate-500">Status</dt><dd>{dataset.metadata?.status ?? '—'}</dd></div>
            </dl>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <MetricCard label="Questions" value={questions.length} />
              <MetricCard label="Avg validation rate" value={passedRate} tone={passedRate === '—' ? 'default' : 'success'} />
            </div>
          </div>

          {questions.length > 0 && (
            <section className="mt-8 grid gap-6 lg:grid-cols-3">
              <BarChartRc title="By Question Type" data={typeData} color="#3f6b4f" />
              <BarChartRc title="By Sensitivity" data={provData} color="#8b5cf6" />
              <BarChartRc title="By Validation Score" data={scoreData} color="#10b981" />
            </section>
          )}

          <section className="mt-8">
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <h2 className="font-semibold">
                Questions
                {editCount > 0 && (
                  <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                    {editCount} unsaved edit{editCount > 1 ? 's' : ''}
                  </span>
                )}
              </h2>
              <div className="flex items-center gap-2 text-sm">
                <button type="button" className="btn-secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                  Prev
                </button>
                <span className="text-slate-500">Page</span>
                <input
                  type="number"
                  min={1}
                  max={numPages}
                  value={page}
                  onChange={(e) => {
                    const p = parseInt(e.target.value, 10);
                    if (!Number.isNaN(p)) setPage(Math.min(numPages, Math.max(1, p)));
                  }}
                  className="input-field w-16 text-center"
                />
                <span className="text-slate-500">/ {numPages}</span>
                <button type="button" className="btn-secondary" disabled={page >= numPages} onClick={() => setPage((p) => p + 1)}>
                  Next
                </button>
              </div>
            </div>

            {pageQuestions.map((q, i) => {
              const globalIdx = start + i;
              const edit = edits[globalIdx] ?? {};
              const passed = isValidationPassed(q);
              return (
                <div key={globalIdx} className="card mb-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="font-medium">
                      Question {globalIdx + 1}
                      <span className="ml-2 text-sm font-normal text-slate-500">Chunk {q.chunk_id ?? '—'}</span>
                    </h4>
                    {passed != null && (
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${passed ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-800'}`}>
                        {passed ? 'Passed' : 'Failed'}
                      </span>
                    )}
                  </div>

                  {q.source_chunk && (
                    <details className="mt-2 text-xs">
                      <summary className="cursor-pointer text-gena-primary">Source text</summary>
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3">{q.source_chunk}</pre>
                    </details>
                  )}

                  {expert ? (
                    <div className="mt-3 space-y-3">
                      <div className="grid gap-3 sm:grid-cols-3">
                        <select className="input-field" value={edit.question_type ?? q.question_type ?? 'open'} onChange={(e) => updateEdit(globalIdx, { question_type: e.target.value })}>
                          {['one', 'multi', 'open'].map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                        <select className="input-field" value={String(edit.provocativeness ?? q.sensitivity_level ?? q.provocativeness ?? '2')} onChange={(e) => updateEdit(globalIdx, { provocativeness: e.target.value })}>
                          {['1', '2', '3'].map((t) => <option key={t} value={t}>Sensitivity {levelLabel(t)}</option>)}
                        </select>
                        <select className="input-field" value={String(edit.difficulty ?? q.difficulty_level ?? q.difficulty ?? '—')} onChange={(e) => updateEdit(globalIdx, { difficulty: e.target.value === '—' ? null : e.target.value })}>
                          {['—', '1', '2', '3'].map((t) => <option key={t} value={t}>{t === '—' ? 'Difficulty —' : `Difficulty ${levelLabel(t)}`}</option>)}
                        </select>
                      </div>
                      <textarea className="input-field" rows={3} value={edit.task ?? q.task ?? ''} onChange={(e) => updateEdit(globalIdx, { task: e.target.value })} />
                      <textarea className="input-field font-mono text-xs" rows={4} value={edit.options != null ? (typeof edit.options === 'string' ? edit.options : asOptionsText(edit.options)) : asOptionsText(q.options)} onChange={(e) => updateEdit(globalIdx, { options: e.target.value })} />
                      <textarea className="input-field" rows={2} value={edit.correct_answer ?? String(q.correct_answer ?? '')} onChange={(e) => updateEdit(globalIdx, { correct_answer: e.target.value })} />
                    </div>
                  ) : (
                    <div className="mt-2 space-y-1 text-sm">
                      <p><strong>Type:</strong> {q.question_type}</p>
                      <p><strong>Task:</strong> {q.task}</p>
                      <pre className="whitespace-pre-wrap rounded bg-slate-50 p-2">{normOptions(q.options)}</pre>
                      <p><strong>Answer:</strong> {q.correct_answer}</p>
                    </div>
                  )}

                  {(q.validation_passed != null || q.validation_score) && (
                    <div className="mt-3 text-sm">
                      <span className={passed ? 'text-emerald-700' : 'text-red-700'}>
                        Validation: {q.validation_score} (thr: {q.validation_threshold})
                      </span>
                      <p className="text-xs text-slate-500">{formatRetryLine(q.retry_count)}</p>
                      {q.validation_details != null && (
                        <ul className="mt-1 text-xs text-slate-600">
                          {formatValidationBreakdown(
                            parseValidationDetails(q.validation_details),
                            parseValidationJustifications(q.validation_justifications),
                          ).map((line, li) => <li key={li}>{line}</li>)}
                        </ul>
                      )}
                    </div>
                  )}
                </div>
              );
            })}

            {expert && (
              <div className="space-y-3">
                <button type="button" className="btn-primary" disabled={saveMutation.isPending || editCount === 0} onClick={() => saveMutation.mutate()}>
                  {saveMutation.isPending ? 'Saving…' : 'Save Changes as New Version'}
                </button>
                {saveMutation.isSuccess && (
                  <StatusBanner tone="success">Saved as version {saveMutation.data?.new_version}.</StatusBanner>
                )}
                {saveMutation.isError && (
                  <StatusBanner tone="error">
                    Save failed: {saveMutation.error instanceof Error ? saveMutation.error.message : 'unknown error'}
                  </StatusBanner>
                )}
              </div>
            )}
            {!expert && <p className="text-sm text-slate-500">Read-only: editing requires expert role.</p>}
          </section>

          <section className="mt-8 card">
            <h2 className="font-semibold">Export</h2>
            <div className="mt-3 flex flex-wrap gap-3">
              <button type="button" className="btn-secondary" onClick={() => void exportResultsXlsx()}>Download XLSX (Generation Results)</button>
              <button type="button" className="btn-secondary" onClick={() => void exportPipelineXlsx()}>Download XLSX (Full Pipeline)</button>
            </div>
          </section>

          {versions.length > 1 && (
            <section className="mt-8 card">
              <h2 className="font-semibold">Version Comparison</h2>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <select className="input-field" value={compareV1 ?? versions[0]?.version ?? ''} onChange={(e) => setCompareV1(parseInt(e.target.value, 10))}>
                  {versions.map((v) => <option key={v.version} value={v.version}>Version {v.version}</option>)}
                </select>
                <select className="input-field" value={compareV2 ?? versions[1]?.version ?? ''} onChange={(e) => setCompareV2(parseInt(e.target.value, 10))}>
                  {versions.map((v) => <option key={v.version} value={v.version}>Version {v.version}</option>)}
                </select>
              </div>
              <button type="button" className="btn-secondary mt-3" onClick={() => setShowCompare(true)}>Compare Versions</button>
              {showCompare && compareResult && (
                <div className="mt-4 space-y-3">
                  {!compareResult.length ? (
                    <p className="text-sm text-emerald-700">No differences between selected versions.</p>
                  ) : (
                    <>
                      <p className="text-sm text-slate-600">{compareResult.length} differing question(s)</p>
                      {compareResult.map((d) => (
                        <details key={d.qid} className="rounded-lg border border-slate-200 p-3">
                          <summary className="cursor-pointer font-medium">Question {d.qid}</summary>
                          <div className="mt-2 space-y-2 text-sm">
                            {Object.entries(d.diffs).map(([field, [v1, v2]]) => (
                              <div key={field} className="grid gap-2 sm:grid-cols-2">
                                <div className="rounded bg-slate-50 p-2"><div className="text-xs text-slate-500">v{compareV1} · {field}</div><pre className="mt-1 whitespace-pre-wrap">{v1 || '—'}</pre></div>
                                <div className="rounded bg-slate-50 p-2"><div className="text-xs text-slate-500">v{compareV2} · {field}</div><pre className="mt-1 whitespace-pre-wrap">{v2 || '—'}</pre></div>
                              </div>
                            ))}
                          </div>
                        </details>
                      ))}
                    </>
                  )}
                </div>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}
