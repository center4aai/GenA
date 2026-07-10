import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listDatasets,
  getDataset,
  listDatasetVersions,
  updateDataset,
} from '@/shared/api/dataset';
import { rephraseQuestions } from '@/shared/api/agent';
import type { Question } from '@/shared/types/documents';
import { shuffleQuestions } from '@/shared/lib/shuffleOptions';
import { normOptions } from '@/shared/lib/optionsFormat';
import { useAuth } from '@/features/auth/authStore';
import { PageHeader } from '@/shared/ui/PageHeader';
import { StatusBanner } from '@/shared/ui/StatusBanner';

const PAGE_SIZE = 10;

export function DynamicImplementationPage() {
  const auth = useAuth();
  const queryClient = useQueryClient();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [shuffleMode, setShuffleMode] = useState(false);
  const [rephraseMode, setRephraseMode] = useState(false);
  const [localQuestions, setLocalQuestions] = useState<Question[] | null>(null);
  const [page, setPage] = useState(1);
  const [message, setMessage] = useState<string | null>(null);

  const { data: datasets = [] } = useQuery({ queryKey: ['datasets'], queryFn: listDatasets });

  const activeId = selectedId ?? datasets[0]?._id ?? null;

  const { data: versions = [] } = useQuery({
    queryKey: ['dataset-versions', activeId],
    queryFn: () => listDatasetVersions(activeId!),
    enabled: !!activeId,
  });

  const version = selectedVersion ?? versions[0]?.version ?? null;

  const { data: dataset } = useQuery({
    queryKey: ['dataset', activeId, version],
    queryFn: () => getDataset(activeId!, version ?? undefined),
    enabled: !!activeId && version != null,
  });

  const questions = localQuestions ?? dataset?.questions ?? [];
  const numPages = Math.max(1, Math.ceil(questions.length / PAGE_SIZE));
  const start = (page - 1) * PAGE_SIZE;
  const pageQuestions = questions.slice(start, start + PAGE_SIZE);

  const transformMutation = useMutation({
    mutationFn: async () => {
      if (!shuffleMode && !rephraseMode) throw new Error('Choose at least one mode');
      let result = [...questions];

      if (rephraseMode) {
        const dsName = datasets.find((d) => d._id === activeId)?.name ?? '';
        const resp = await rephraseQuestions(dsName, result);
        if (resp.status !== 'success' || !resp.result) {
          throw new Error('Rephrase failed');
        }
        result = resp.result;
      }

      if (shuffleMode) {
        result = shuffleQuestions(result);
      }

      return result;
    },
    onSuccess: (result) => {
      setLocalQuestions(result);
      setPage(1);
      setMessage('Transformations applied. Review and save as new version.');
    },
    onError: (e) => setMessage(e instanceof Error ? e.message : 'Transform failed'),
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const normalized = questions.map((q) => {
        const difficultyRaw = q.difficulty_level ?? q.difficulty;
        const difficulty =
          difficultyRaw == null || String(difficultyRaw).trim() === '' ? null : String(difficultyRaw);
        return {
          question_id: q.question_id,
          chunk_id: q.chunk_id,
          source_chunk: q.source_chunk,
          question_type: q.question_type != null ? String(q.question_type) : q.question_type,
          task: q.task != null ? String(q.task) : q.task,
          options: q.options,
          correct_answer: q.correct_answer != null ? String(q.correct_answer) : q.correct_answer,
          provocativeness: q.sensitivity_level ?? q.provocativeness,
          difficulty,
          validation_passed: q.validation_passed,
          validation_score: q.validation_score,
          validation_threshold: q.validation_threshold,
          validation_details: q.validation_details,
          validation_justifications: q.validation_justifications,
          retry_count: q.retry_count,
        };
      });
      const metadata = {
        ...(dataset?.metadata ?? {}),
        edited_at: new Date().toISOString(),
        edited_by: auth.username ?? 'expert',
        source_version: version,
      };
      return updateDataset(activeId!, { questions: normalized, metadata });
    },
    onSuccess: (res) => {
      setMessage(`Saved as version ${res.new_version}`);
      setLocalQuestions(null);
      queryClient.invalidateQueries({ queryKey: ['dataset', activeId] });
      queryClient.invalidateQueries({ queryKey: ['dataset-versions', activeId] });
    },
    onError: (e) => setMessage(e instanceof Error ? e.message : 'Save failed'),
  });

  if (!datasets.length) {
    return (
      <div>
        <PageHeader title="Dynamic Implementation" subtitle="Shuffle and rephrase questions to create alternative dataset variants." />
        <p className="mt-4 text-slate-600">No datasets found.</p>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Dynamic Implementation"
        subtitle="Shuffle and rephrase questions using LLM to create alternative dataset variants."
      />
      {message && <div className="mt-4"><StatusBanner tone="info">{message}</StatusBanner></div>}

      <div className="mt-6 space-y-4">
        <select
          className="input-field max-w-xl"
          value={activeId ?? ''}
          onChange={(e) => {
            setSelectedId(e.target.value);
            setSelectedVersion(null);
            setLocalQuestions(null);
            setPage(1);
          }}
        >
          {datasets.map((ds) => (
            <option key={ds._id} value={ds._id}>
              {ds.name} (v{ds.current_version})
            </option>
          ))}
        </select>

        {versions.length > 0 && (
          <select
            className="input-field max-w-xs"
            value={version ?? ''}
            onChange={(e) => {
              setSelectedVersion(parseInt(e.target.value, 10));
              setLocalQuestions(null);
              setPage(1);
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

      <section className="mt-6 card">
        <h2 className="font-semibold">Choose options</h2>
        <div className="mt-3 flex flex-wrap gap-6">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={shuffleMode} onChange={(e) => setShuffleMode(e.target.checked)} />
            Shuffle the response options
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={rephraseMode} onChange={(e) => setRephraseMode(e.target.checked)} />
            Rephrase the questions
          </label>
        </div>
        <button
          type="button"
          className="btn-primary mt-4"
          disabled={transformMutation.isPending}
          onClick={() => transformMutation.mutate()}
        >
          {transformMutation.isPending ? 'Processing…' : 'Go'}
        </button>
      </section>


      {questions.length > 0 && (
        <section className="mt-8">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="font-semibold">Questions</h2>
            <div className="flex gap-2 text-sm">
              <button type="button" className="btn-secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
              <span>{page}/{numPages}</span>
              <button type="button" className="btn-secondary" disabled={page >= numPages} onClick={() => setPage((p) => p + 1)}>Next</button>
            </div>
          </div>

          {pageQuestions.map((q, i) => (
            <div key={start + i} className="card mb-4 text-sm">
              <h4 className="font-medium">Question {start + i + 1} (Chunk {q.chunk_id ?? '—'})</h4>
              <p><strong>Type:</strong> {q.question_type}</p>
              <p><strong>Sensitivity:</strong> {q.sensitivity_level ?? q.provocativeness ?? '—'}</p>
              <p><strong>Difficulty:</strong> {q.difficulty_level ?? q.difficulty ?? '—'}</p>
              <p><strong>Task:</strong> {q.task}</p>
              <pre className="mt-1 whitespace-pre-wrap rounded bg-slate-50 p-2">{normOptions(q.options)}</pre>
              <p><strong>Correct:</strong> {q.correct_answer}</p>
              {q.source_chunk && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer text-gena-primary">Source chunk</summary>
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2">{q.source_chunk}</pre>
                </details>
              )}
            </div>
          ))}

          <button
            type="button"
            className="btn-primary"
            disabled={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending
              ? 'Saving…'
              : localQuestions
                ? 'Save Changes as New Version'
                : 'Save Current Version as New Version'}
          </button>
        </section>
      )}
    </div>
  );
}
