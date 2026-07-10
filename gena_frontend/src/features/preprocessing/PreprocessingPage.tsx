import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  listDatasets,
  createDataset,
  createQueue,
  addQueueTasks,
  saveDatasetChunks,
  listDatasetTasks,
  listDatasetChunks,
} from '@/shared/api/dataset';
import { listModels, listModelsHealth, runChunkGate, processPrompt } from '@/shared/api/agent';
import { chunkDocument } from '@/shared/api/chunker';
import type { ChunkResponse, Dataset, GateResult } from '@/shared/types/documents';
import {
  buildChunkResponseFromStored,
  chunkPreviewText,
  extractChunks,
} from '@/shared/lib/normalizeChunk';
import { disabledGateResult, evaluateGatePass, gateRejectionSummary } from '@/shared/lib/gateDecision';
import { anyActiveGeneration } from '@/shared/lib/progress';
import { optionsDictFromGenerated, optionsToNumberedList } from '@/shared/lib/optionsFormat';
import { formatRetryLine, formatValidationBreakdown, parseValidationDetails, parseValidationJustifications } from '@/shared/lib/validation';
import { downloadCsv } from '@/shared/lib/extendedExport';
import { truncIso } from '@/shared/lib/labels';
import { usePagedList } from '@/shared/lib/usePagedList';
import { useSort } from '@/shared/lib/useSort';
import { useResizableColumns } from '@/shared/lib/useResizableColumns';
import { PageHeader } from '@/shared/ui/PageHeader';
import { Pagination } from '@/shared/ui/Pagination';
import { SortableTh } from '@/shared/ui/SortableTh';
import { StatusBanner, LoadingState } from '@/shared/ui/StatusBanner';
const PIPELINE_MODES: Record<string, string> = {
  generator_validator: 'Generator + validator (no gate, no refine)',
  generator_validator_gate: 'Generator + validator + gate (no refine)',
  full: 'Full pipeline (gate + refine)',
};

const QUESTION_TYPES = ['one', 'multi', 'open'] as const;

function pipelineGateEnabled(mode: string) {
  return mode !== 'generator_validator';
}

function pipelineRefineEnabled(mode: string) {
  return mode === 'full';
}

// Cache chunk responses by `${name}_${size}` so re-uploading the same file is instant,
// and persist direct-processing results across navigation.
const chunkCache = new Map<string, ChunkResponse>();
let persistedDirectResults: DirectResult[] = [];

interface DirectResult {
  chunkId: number;
  sourceChunk: string;
  questionType: string;
  task: string;
  options: string;
  optionsDict: Record<string, string>;
  correctAnswer: string;
  provocativeness: string;
  validationScore: string;
  validationThreshold: string;
  validationPassed: boolean;
  validationDetails: unknown;
  validationJustifications: unknown;
  retryCount: number;
}

export function PreprocessingPage() {
  const [selectedDsId, setSelectedDsId] = useState('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [chunkData, setChunkData] = useState<ChunkResponse | null>(null);
  const [documentName, setDocumentName] = useState('');
  const [datasetName, setDatasetName] = useState('');
  const [datasetDescription, setDatasetDescription] = useState('');
  const [questionTypes, setQuestionTypes] = useState<string[]>([...QUESTION_TYPES]);
  const [genModelId, setGenModelId] = useState('');
  const [valModelId, setValModelId] = useState('');
  const [processingMode, setProcessingMode] = useState<'queue' | 'direct'>('queue');
  const [pipelineMode, setPipelineMode] = useState('full');
  const [ablationTesting, setAblationTesting] = useState(false);
  const [chunking, setChunking] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [gateProgress, setGateProgress] = useState<string | null>(null);
  const [gateSummary, setGateSummary] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  // The dataset name we launched during this session, so we stop nagging about
  // a "duplicate" name that we ourselves just created a moment ago.
  const [lastRunName, setLastRunName] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [directResults, setDirectResults] = useState<DirectResult[]>(persistedDirectResults);
  const [savedDataset, setSavedDataset] = useState(false);
  const [showChunks, setShowChunks] = useState(false);
  const [chunkProgress, setChunkProgress] = useState<{ done: number; total: number } | null>(null);
  const [dsSearch, setDsSearch] = useState('');

  const { data: datasets = [], refetch: refetchDatasets, isLoading: loadingDatasets } = useQuery({
    queryKey: ['datasets'],
    queryFn: listDatasets,
  });

  const persistResults = (results: DirectResult[]) => {
    persistedDirectResults = results;
    setDirectResults(results);
  };

  const { data: models = [] } = useQuery({ queryKey: ['models'], queryFn: listModels });
  const { data: health = [] } = useQuery({ queryKey: ['models-health'], queryFn: listModelsHealth });

  const healthMap = useMemo(() => Object.fromEntries(health.map((h) => [h.id, h.available])), [health]);

  const effectiveGenModelId = genModelId || models[0]?.id || '';
  const effectiveValModelId = valModelId || models[0]?.id || '';

  useEffect(() => {
    if (models.length && !genModelId) setGenModelId(models[0].id);
    if (models.length && !valModelId) setValModelId(models[0].id);
  }, [models, genModelId, valModelId]);

  // Proactive active-generation check: block generation while another run is processing.
  const { data: activeGen = { active: false, queueName: '' } } = useQuery({
    queryKey: ['active-generation', datasets.map((d) => `${d._id}:${d.metadata?.status}`).join(',')],
    queryFn: async () => {
      const tasksByDataset: Record<string, Awaited<ReturnType<typeof listDatasetTasks>>> = {};
      for (const ds of datasets) {
        if (ds.metadata?.status === 'processing') {
          try {
            tasksByDataset[ds._id] = await listDatasetTasks(ds._id);
          } catch {
            tasksByDataset[ds._id] = [];
          }
        }
      }
      return anyActiveGeneration(datasets, tasksByDataset);
    },
    enabled: datasets.length > 0,
    refetchInterval: 15_000,
  });

  const chosenDs = selectedDsId ? (datasets.find((d) => d._id === selectedDsId) ?? null) : null;
  const usingExisting = chosenDs !== null;

  const effectiveRunName =
    ablationTesting && !usingExisting ? `${datasetName}__${pipelineMode}` : datasetName;
  const duplicateName =
    !usingExisting &&
    !!datasetName &&
    effectiveRunName !== lastRunName &&
    datasets.some((d) => d.name === effectiveRunName);

  const dsSort = useSort<Dataset>();
  const dsCols = useResizableColumns({ name: 260, source: 220, id: 220 });
  const dsSorted = dsSort.sortItems(datasets, {
    name: (d) => d.name,
    source: (d) => d.source_document,
    chunks: (d) => d.chunks_valid ?? 0,
    questions: (d) => d.questions?.length ?? 0,
    status: (d) => d.metadata?.status,
    created: (d) => d.created_at,
    id: (d) => d._id,
  });
  const dsPaged = usePagedList(dsSorted, {
    pageSize: 8,
    search: dsSearch,
    searchFields: (d) => [d.name, d.source_document, d.metadata?.status, d._id],
  });

  const loadExistingChunks = useMutation({
    mutationFn: async (ds: Dataset) => {
      const stored = await listDatasetChunks(ds._id, false);
      return buildChunkResponseFromStored(stored);
    },
    onSuccess: (data, ds) => {
      setChunkData(data);
      setDocumentName(ds.source_document ?? ds.name);
      setDatasetName(ds.name);
      setDatasetDescription(ds.description ?? '');
      if (!data.num_chunks) {
        setErrorMsg(`Dataset '${ds.name}' has no stored chunks. Re-run chunking on the source document.`);
      }
    },
    onError: (e) => {
      setChunkData(null);
      setErrorMsg(e instanceof Error ? e.message : 'Failed to load stored chunks');
    },
  });

  const handleFileUpload = async (file: File) => {
    setUploadFile(file);
    setErrorMsg(null);
    const cacheKey = `${file.name}_${file.size}`;
    const cached = chunkCache.get(cacheKey);
    if (cached) {
      setChunkData(cached);
      setDocumentName(cached.document_type?.document_name ?? file.name.replace(/\.[^.]+$/, ''));
      setDatasetName(file.name.replace(/\.[^.]+$/, ''));
      setDatasetDescription(`Generated from ${file.name}`);
      return;
    }
    setChunking(true);
    try {
      const data = await chunkDocument(file);
      const docName =
        data.document_type?.document_name ?? file.name.replace(/\.[^.]+$/, '');
      chunkCache.set(cacheKey, data);
      setChunkData(data);
      setDocumentName(docName);
      setDatasetName(file.name.replace(/\.[^.]+$/, ''));
      setDatasetDescription(`Generated from ${file.name}`);
      if (!data.num_chunks) {
        setErrorMsg('Chunking produced 0 chunks. The document may be empty or unsupported.');
      }
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Chunking failed');
      setChunkData(null);
    } finally {
      setChunking(false);
    }
  };

  const handleGenerate = async () => {
    if (!chunkData || !chunkData.num_chunks || !questionTypes.length) return;
    if (duplicateName) {
      setErrorMsg(
        `A dataset named "${effectiveRunName}" already exists. Pick a unique name or re-use the existing dataset above.`,
      );
      return;
    }
    setErrorMsg(null);
    setStatusMsg(null);
    setGateSummary(null);
    setGenerating(true);

    try {
      // Running generations no longer block a new run — the queue processes
      // everything in the background, so concurrent runs are allowed. Any
      // in-progress work is surfaced as a non-blocking notice instead.
      const gateEnabled = pipelineGateEnabled(pipelineMode);
      const datasetRunName =
        ablationTesting && !usingExisting ? `${datasetName}__${pipelineMode}` : datasetName;
      const extracted = extractChunks(chunkData.chunks_detailed, chunkData.chunks);

      if (!extracted.length) {
        setErrorMsg('All chunks are empty or too short.');
        setGenerating(false);
        return;
      }

      if (processingMode === 'queue') {
        const gateResults: Record<number, GateResult> = {};
        let validChunks: Array<(typeof extracted)[0] & { typesOk: string[]; gate: GateResult }> = [];
        let rejected = 0;

        if (!gateEnabled) {
          validChunks = extracted.map((ec) => {
            const gate = disabledGateResult();
            gateResults[ec.idx] = gate;
            return { ...ec, typesOk: [...questionTypes], gate };
          });
          setGateSummary(`Gate disabled. Using all ${validChunks.length}/${extracted.length} chunks.`);
        } else if (usingExisting) {
          for (const ec of extracted) {
            const storedTypes = ec.typesValid ?? [...questionTypes];
            const typesOk = questionTypes.filter((qt) => storedTypes.includes(qt));
            if (typesOk.length) {
              const gate = (ec.gateResult as GateResult) ?? { passed: true };
              gateResults[ec.idx] = gate;
              validChunks.push({ ...ec, typesOk, gate });
            }
          }
          rejected = extracted.length - validChunks.length;
          setGateSummary(`${validChunks.length}/${extracted.length} chunks passed gate, ${rejected} rejected`);
        } else {
          const gateQtype = questionTypes.includes('multi') ? 'multi' : questionTypes[0];
          for (let gi = 0; gi < extracted.length; gi++) {
            const ec = extracted[gi];
            setChunkProgress({ done: gi + 1, total: extracted.length });
            setGateProgress(`Gate: chunk ${ec.idx} (${gi + 1}/${extracted.length})…`);
            try {
              const res = await runChunkGate({
                chunk: ec.text,
                question_type: gateQtype,
                ...(effectiveValModelId ? { validation_model_id: effectiveValModelId } : {}),
              });
              gateResults[ec.idx] = res.result ?? { passed: false };
            } catch (e) {
              gateResults[ec.idx] = { passed: false, rejection_reason: `gate_error: ${e}` };
            }
          }
          setGateProgress(null);
          setChunkProgress(null);

          for (const ec of extracted) {
            const gr = gateResults[ec.idx] ?? {};
            const { typesOk } = evaluateGatePass(gr, questionTypes);
            if (typesOk.length) {
              validChunks.push({ ...ec, typesOk, gate: gr });
            }
          }
          rejected = extracted.length - validChunks.length;
          const summary = gateRejectionSummary(gateResults);
          setGateSummary(
            `${validChunks.length}/${extracted.length} chunks passed gate, ${rejected} rejected` +
              (summary ? `. Rejections: ${summary}` : ''),
          );
        }

        if (!validChunks.length) {
          setErrorMsg('All chunks were rejected by the gate.');
          setGenerating(false);
          return;
        }

        const ts = new Date().toISOString().replace(/\D/g, '').slice(0, 14);
        const queueNameNew = `queue_${datasetRunName}_${ts}`;
        await createQueue({ name: queueNameNew, description: `Queue for ${datasetRunName}`, priority: 1 });

        const validByIdx = Object.fromEntries(validChunks.map((vc) => [vc.idx, vc]));
        let dsId: string;

        if (usingExisting && chosenDs) {
          dsId = chosenDs._id;
          setStatusMsg(`Re-using existing dataset '${datasetName}' (${dsId})`);
        } else {
          const totalTasksPlanned = validChunks.reduce((s, vc) => s + vc.typesOk.length, 0);
          const created = await createDataset({
            name: datasetRunName,
            description: datasetDescription,
            source_document: documentName,
            questions: [],
            metadata: {
              ablation_testing: ablationTesting,
              queue_name: queueNameNew,
              pipeline_mode: pipelineMode,
              gate_enabled: gateEnabled,
              refine_enabled: pipelineRefineEnabled(pipelineMode),
              experiment_source_document: documentName,
              question_types: questionTypes,
              num_chunks_processed: 0,
              total_chunks: chunkData.num_chunks,
              chunks_passed_gate: validChunks.length,
              chunks_rejected: rejected,
              total_questions_generated: 0,
              questions_per_chunk: questionTypes.length,
              expected_questions: totalTasksPlanned,
              generation_model_id: effectiveGenModelId || undefined,
              validation_model_id: effectiveValModelId || undefined,
              created_at: new Date().toISOString(),
              status: 'processing',
            },
          });
          dsId = created.dataset_id;

          const chunkDocs = extracted.map((ec) => {
            const vc = validByIdx[ec.idx];
            const gateResult =
              vc?.gate ?? gateResults[ec.idx] ?? { passed: false, rejection_reason: 'not_selected_for_generation' };
            return {
              chunk_index: ec.idx,
              chunk_text: ec.text,
              fragment_data: ec.fragmentData,
              gate_result: gateResult,
              gate_passed: Boolean(vc),
              question_types_valid: vc?.typesOk ?? [],
            };
          });
          await saveDatasetChunks(dsId, chunkDocs);
        }

        const tasks = validChunks.flatMap((vc) =>
          vc.typesOk.map((qt) => ({
            chunk_id: vc.idx,
            chunk_text: vc.text,
            question_type: qt,
            source_document: documentName,
            dataset_name: datasetRunName,
            dataset_id: dsId,
            dataset_description: datasetDescription,
            priority: 1,
            chunk_pre_validated: true,
            pipeline_mode: pipelineMode,
            ...(effectiveGenModelId ? { generation_model_id: effectiveGenModelId } : {}),
            ...(effectiveValModelId ? { validation_model_id: effectiveValModelId } : {}),
          })),
        );

        const tr = await addQueueTasks(queueNameNew, tasks);
        setLastRunName(datasetRunName);
        setStatusMsg(`Added ${tr.tasks_added} tasks to queue '${queueNameNew}'. Open Queue Manager to monitor.`);
        refetchDatasets();
      } else {
        const totalQuestions = chunkData.num_chunks * questionTypes.length;
        const results: DirectResult[] = [];
        let counter = 0;
        const items = chunkData.chunks_detailed ?? chunkData.chunks ?? [];

        for (let idx = 0; idx < items.length; idx++) {
          const chunkText = chunkPreviewText(items[idx] as never);
          for (const qt of questionTypes) {
            counter++;
            setChunkProgress({ done: counter, total: totalQuestions });
            setGateProgress(`Processing chunk ${idx + 1}/${chunkData.num_chunks}, type ${qt} (${counter}/${totalQuestions})…`);
            try {
              const res = await processPrompt({
                prompt: chunkText,
                question_type: qt,
                source: 'user_input',
                chat_id: 12345,
                source_text: chunkText,
                pipeline_mode: pipelineMode,
                ...(effectiveGenModelId ? { generation_model_id: effectiveGenModelId } : {}),
                ...(effectiveValModelId ? { validation_model_id: effectiveValModelId } : {}),
                ...(usingExisting ? { chunk_pre_validated: true } : {}),
              });
              const output = (res.result?.output ?? {}) as Record<string, unknown>;
              if (output.chunk_rejected) continue;

              const gq = (output.generated_question ?? {}) as Record<string, unknown>;
              const sensitivity = (output.sensitivity_score ?? {}) as Record<string, unknown>;
              const validation = (output.validation_result ?? {}) as Record<string, unknown>;

              results.push({
                chunkId: idx + 1,
                sourceChunk: chunkText,
                questionType: String(output.question_type ?? qt),
                task: String(gq.task ?? ''),
                options: optionsToNumberedList(gq),
                optionsDict: optionsDictFromGenerated(gq),
                correctAnswer: String(gq.outputs ?? 'N/A'),
                provocativeness: String(sensitivity.provocativeness_score ?? 'N/A'),
                validationScore: `${validation.total ?? 'N/A'}/${validation.max_total ?? 'N/A'}`,
                validationThreshold: String(validation.threshold ?? 'N/A'),
                validationPassed: Boolean(validation.passed),
                validationDetails: validation.by_block,
                validationJustifications: validation.justifications,
                retryCount: Number(output.retry_count ?? 0),
              });
            } catch (e) {
              results.push({
                chunkId: idx + 1,
                sourceChunk: chunkText,
                questionType: qt,
                task: `Error: ${e instanceof Error ? e.message : 'unknown'}`,
                options: '',
                optionsDict: {},
                correctAnswer: '',
                provocativeness: '',
                validationScore: 'N/A',
                validationThreshold: 'N/A',
                validationPassed: false,
                validationDetails: {},
                validationJustifications: {},
                retryCount: 0,
              });
            }
          }
        }
        persistResults(results);
        setChunkProgress(null);
        setGateProgress(null);
        setStatusMsg('Direct processing completed.');
      }
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  const handleSaveDirect = async () => {
    if (!directResults.length) return;
    const datasetRunName =
      ablationTesting && !usingExisting ? `${datasetName}__${pipelineMode}` : datasetName;
    try {
      const questions = directResults.map((item) => ({
        chunk_id: item.chunkId,
        source_chunk: item.sourceChunk,
        question_type: item.questionType,
        task: item.task,
        options: item.optionsDict,
        correct_answer: item.correctAnswer,
        provocativeness: item.provocativeness,
        validation_score: item.validationScore,
        validation_threshold: item.validationThreshold,
        validation_passed: String(item.validationPassed),
        validation_details:
          typeof item.validationDetails === 'string'
            ? item.validationDetails
            : JSON.stringify(item.validationDetails ?? {}),
        validation_justifications:
          typeof item.validationJustifications === 'string'
            ? item.validationJustifications
            : JSON.stringify(item.validationJustifications ?? {}),
        retry_count: String(item.retryCount),
      }));
      const res = await createDataset({
        name: datasetRunName,
        description: datasetDescription,
        source_document: documentName,
        questions,
        metadata: {
          ablation_testing: ablationTesting,
          pipeline_mode: pipelineMode,
          gate_enabled: pipelineGateEnabled(pipelineMode),
          refine_enabled: pipelineRefineEnabled(pipelineMode),
          question_types: questionTypes,
          experiment_source_document: documentName,
          total_chunks: chunkData?.num_chunks,
          num_chunks_processed: chunkData?.num_chunks ?? 0,
          questions_per_chunk: questionTypes.length,
          total_questions_generated: directResults.length,
          generation_model_id: effectiveGenModelId || undefined,
          validation_model_id: effectiveValModelId || undefined,
          generated_at: new Date().toISOString(),
        },
      });
      setSavedDataset(true);
      setStatusMsg(`Dataset saved! ID: ${res.dataset_id}`);
      refetchDatasets();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Save failed');
    }
  };

  const hasData = chunkData != null && (chunkData.num_chunks ?? 0) > 0;
  const previewItems = chunkData?.chunks_detailed ?? chunkData?.chunks ?? [];

  if (loadingDatasets) return <LoadingState label="Loading datasets…" />;

  return (
    <div>
      <PageHeader
        title="Data Preprocessing"
        subtitle="Select an existing dataset or upload a new document, choose question types, and generate a dataset. Results will appear in Results & Editor."
      />

      <div className="mb-4 flex gap-4 text-sm">
        <Link to="/" className="text-gena-primary hover:underline">Home</Link>
        <Link to="/docs" className="text-gena-primary hover:underline">Documentation</Link>
      </div>

      <hr className="my-6" />

      <h2 className="text-lg font-semibold">Existing Datasets</h2>
      {datasets.length ? (
        <>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
            <input
              type="search"
              className="input-field max-w-xs"
              placeholder="Search datasets…"
              value={dsSearch}
              onChange={(e) => { setDsSearch(e.target.value); dsPaged.setPage(1); }}
            />
            <Pagination
              page={dsPaged.page}
              totalPages={dsPaged.totalPages}
              total={dsPaged.total}
              onPageChange={dsPaged.setPage}
              label="datasets"
            />
          </div>
          <div className="mt-2 w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="data-table" ref={dsCols.tableRef} style={dsCols.tableStyle}>
              <thead>
                <tr>
                  <SortableTh label="Name" sortKey="name" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} className="w-64" />
                  <SortableTh label="Source" sortKey="source" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="Chunks" sortKey="chunks" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="Questions" sortKey="questions" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="Status" sortKey="status" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="Created" sortKey="created" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                  <SortableTh label="ID" sortKey="id" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                </tr>
              </thead>
              <tbody>
                {dsPaged.pageItems.map((ds) => (
                  <tr key={ds._id}>
                    <td className="align-top font-medium">
                      <div className="whitespace-normal break-words" title={ds.name}>{ds.name}</div>
                    </td>
                    <td className="whitespace-normal break-words align-top" title={ds.source_document}>{ds.source_document}</td>
                    <td className="whitespace-nowrap">
                      {ds.chunks_valid ?? 0}/{ds.chunks_count ?? ds.metadata?.total_chunks ?? 0}
                    </td>
                    <td>{ds.questions?.length ?? 0}</td>
                    <td>{ds.metadata?.status ?? '—'}</td>
                    <td className="whitespace-nowrap text-xs text-slate-500">{truncIso(ds.created_at)}</td>
                    <td className="whitespace-normal break-all align-top font-mono text-xs text-slate-400" title={ds._id}>{ds._id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <label className="mt-4 block text-sm font-medium">Use an existing dataset</label>
          <select
            className="input-field mt-1 max-w-xl"
            value={selectedDsId}
            onChange={(e) => {
              const id = e.target.value;
              setSelectedDsId(id);
              const ds = datasets.find((d) => d._id === id);
              if (ds) {
                loadExistingChunks.mutate(ds);
              } else {
                setChunkData(null);
              }
            }}
          >
            <option value="">— none (upload new) —</option>
            {dsPaged.filteredItems.map((ds) => (
              <option key={ds._id} value={ds._id}>
                {ds.name} ({ds.source_document})
              </option>
            ))}
          </select>
        </>
      ) : (
        <p className="mt-2 text-slate-600">No datasets found. Upload a document below.</p>
      )}

      <hr className="my-6" />

      {usingExisting && chosenDs ? (
        <div className="space-y-4">
          <p className="rounded-lg bg-green-50 p-3 text-green-800">
            Using existing dataset: <strong>{datasetName}</strong> (source: {documentName})
          </p>
          {loadExistingChunks.isPending && <p className="text-sm text-slate-500">Loading chunks…</p>}
          <div>
            <label className="text-sm font-medium">Dataset Name</label>
            <input className="input-field mt-1" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
          </div>
          <div>
            <label className="text-sm font-medium">Description</label>
            <textarea className="input-field mt-1" rows={2} value={datasetDescription} onChange={(e) => setDatasetDescription(e.target.value)} />
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold">Upload a new document</h2>
            <button
              type="button"
              className="text-sm text-gena-primary hover:underline"
              onClick={() => {
                const sample = [
                  'Example Document',
                  '',
                  'This is an example document for GenA. Replace this text with your own',
                  'content (.docx, .txt or .pdf). The chunker will split it into fragments,',
                  'and the pipeline will generate and validate questions for each chunk.',
                ].join('\n');
                const blob = new Blob([sample], { type: 'text/plain;charset=utf-8' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'example_document.txt';
                a.click();
                URL.revokeObjectURL(url);
              }}
            >
              Download example document
            </button>
          </div>
          <input
            type="file"
            accept=".docx,.txt,.pdf"
            className="block w-full cursor-pointer rounded-lg border border-slate-300 text-sm text-slate-600 file:mr-4 file:cursor-pointer file:border-0 file:bg-gena-primary file:px-4 file:py-2.5 file:text-sm file:font-medium file:text-white hover:file:bg-gena-primary-dark"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void handleFileUpload(f);
            }}
          />
          {uploadFile && <p className="text-green-700">File uploaded: {uploadFile.name}</p>}
          {chunking && <p className="text-sm">Splitting document into chunks…</p>}
          {hasData && (
            <>
              <div>
                <label className="text-sm font-medium">Dataset Name</label>
                <input className="input-field mt-1" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
              </div>
              <div>
                <label className="text-sm font-medium">Description</label>
                <textarea className="input-field mt-1" rows={2} value={datasetDescription} onChange={(e) => setDatasetDescription(e.target.value)} />
              </div>
            </>
          )}
        </div>
      )}

      {hasData && (
        <>
          <button type="button" className="mt-4 text-sm text-gena-primary" onClick={() => setShowChunks(!showChunks)}>
            {showChunks ? 'Hide' : 'Preview'} chunks ({chunkData!.num_chunks})
          </button>
          {showChunks && (
            <div className="mt-2 max-h-96 space-y-2 overflow-y-auto">
              {previewItems.slice(0, 50).map((ch, i) => (
                <div key={i} className="card text-xs">
                  <div className="font-medium">Chunk {i + 1}</div>
                  <pre className="mt-1 whitespace-pre-wrap">{chunkPreviewText(ch as never)}</pre>
                </div>
              ))}
            </div>
          )}

          <hr className="my-6" />
          <h3 className="font-semibold">Question Types</h3>
          <div className="mt-2 flex flex-wrap gap-3">
            {QUESTION_TYPES.map((qt) => (
              <label key={qt} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={questionTypes.includes(qt)}
                  onChange={(e) => {
                    setQuestionTypes((prev) =>
                      e.target.checked ? [...prev, qt] : prev.filter((x) => x !== qt),
                    );
                  }}
                />
                {qt}
              </label>
            ))}
          </div>
          {questionTypes.length === 0 && (
            <p className="mt-2 text-sm text-amber-700">
              Please select at least one question type — the Generate button stays disabled until you do.
            </p>
          )}

          {models.length > 0 && (
            <>
              <hr className="my-6" />
              <h3 className="font-semibold">Model Selection</h3>
              <div className="mt-2 grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="text-sm">Generation model</label>
                  <select
                    className="input-field mt-1"
                    value={effectiveGenModelId}
                    onChange={(e) => setGenModelId(e.target.value)}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {!healthMap[m.id] ? '⚠ ' : ''}{m.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-sm">Validation model</label>
                  <select
                    className="input-field mt-1"
                    value={effectiveValModelId}
                    onChange={(e) => setValModelId(e.target.value)}
                  >
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {!healthMap[m.id] ? '⚠ ' : ''}{m.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </>
          )}

          <hr className="my-6" />
          <div className="space-y-3">
            <div>
              <span className="text-sm font-medium">Processing Mode</span>
              <div className="mt-1 flex gap-4">
                <label className="text-sm">
                  <input type="radio" checked={processingMode === 'queue'} onChange={() => setProcessingMode('queue')} /> Queue Mode (Recommended)
                </label>
                <label className="text-sm">
                  <input type="radio" checked={processingMode === 'direct'} onChange={() => setProcessingMode('direct')} /> Direct Processing
                </label>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">Pipeline Mode</label>
              <select className="input-field mt-1 max-w-xl" value={pipelineMode} onChange={(e) => setPipelineMode(e.target.value)}>
                {Object.entries(PIPELINE_MODES).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
              <p className="mt-1 text-xs text-slate-500">
                Gate: {pipelineGateEnabled(pipelineMode) ? 'enabled' : 'disabled'}; refine:{' '}
                {pipelineRefineEnabled(pipelineMode) ? 'enabled' : 'disabled'}.
              </p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={ablationTesting} onChange={(e) => setAblationTesting(e.target.checked)} />
              Ablation testing run
            </label>
          </div>

          {duplicateName && !activeGen.active && (
            <div className="mt-4">
              <StatusBanner tone="warning">
                A dataset named <strong>{effectiveRunName}</strong> already exists, so generation is
                blocked to avoid duplicates. Pick a unique name, or re-use the existing dataset from
                the “Use an existing dataset” list above to append to it.
              </StatusBanner>
            </div>
          )}

          {activeGen.active ? (
            <div className="mt-6">
              <StatusBanner tone="info">
                Generation in progress{activeGen.queueName ? ` (queue: ${activeGen.queueName})` : ''}.
                The Generate button is hidden until it finishes — tasks are processed in the
                background queue.{' '}
                <Link to="/queue_manager" className="underline">Open Queue Manager →</Link>
              </StatusBanner>
            </div>
          ) : (
            <button
              type="button"
              className="btn-primary mt-6"
              disabled={!questionTypes.length || generating || duplicateName}
              onClick={() => void handleGenerate()}
            >
              {generating ? 'Generating…' : 'Generate Questions'}
            </button>
          )}
        </>
      )}

      {chunkProgress && (
        <div className="mt-4">
          <div className="mb-1 flex justify-between text-xs text-slate-500">
            <span>Progress</span>
            <span>{chunkProgress.done}/{chunkProgress.total}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full bg-gena-primary transition-all"
              style={{ width: `${chunkProgress.total ? (chunkProgress.done / chunkProgress.total) * 100 : 0}%` }}
            />
          </div>
        </div>
      )}

      {gateProgress && <p className="mt-4 text-sm text-slate-600">{gateProgress}</p>}
      {gateSummary && <div className="mt-4"><StatusBanner tone="info">{gateSummary}</StatusBanner></div>}
      {statusMsg && <div className="mt-4"><StatusBanner tone="success">{statusMsg}</StatusBanner></div>}
      {errorMsg && <div className="mt-4"><StatusBanner tone="error">{errorMsg}</StatusBanner></div>}
      {statusMsg?.includes('queue') && (
        <Link to="/queue_manager" className="mt-2 inline-block text-gena-primary hover:underline">
          Open Queue Manager →
        </Link>
      )}

      {directResults.length > 0 && (
        <div className="mt-8 space-y-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-lg font-semibold">Direct Processing Results</h3>
            <button
              type="button"
              className="btn-secondary"
              onClick={() =>
                downloadCsv(
                  `${effectiveRunName || 'direct_results'}.csv`,
                  directResults.map((r) => ({
                    chunk_id: r.chunkId,
                    question_type: r.questionType,
                    task: r.task,
                    options: r.options,
                    correct_answer: r.correctAnswer,
                    provocativeness: r.provocativeness,
                    validation_score: r.validationScore,
                    validation_threshold: r.validationThreshold,
                    validation_passed: r.validationPassed,
                    retry_count: r.retryCount,
                    source_chunk: r.sourceChunk,
                  })),
                )
              }
            >
              Download Results (CSV)
            </button>
          </div>
          {Object.entries(
            directResults.reduce<Record<number, DirectResult[]>>((acc, r) => {
              (acc[r.chunkId] ??= []).push(r);
              return acc;
            }, {}),
          ).map(([chunkId, items]) => (
            <div key={chunkId} className="card">
              <h4 className="font-semibold">Chunk #{chunkId}</h4>
              {items.map((item, i) => (
                <div key={i} className="mt-4 border-t pt-4">
                  <p className="text-sm text-slate-500">Question {i + 1} ({item.questionType})</p>
                  <p><strong>Task:</strong> {item.task}</p>
                  <pre className="mt-1 text-sm">{item.options}</pre>
                  <p><strong>Correct:</strong> {item.correctAnswer}</p>
                  <p className="text-sm text-slate-600">Provocativeness: {item.provocativeness}</p>
                  <p className="text-sm">
                    Validation: {item.validationScore}
                    {item.validationThreshold && item.validationThreshold !== 'N/A'
                      ? ` (threshold ${item.validationThreshold})`
                      : ''}{' '}
                    — {item.validationPassed ? 'PASSED' : 'FAILED'}
                  </p>
                  <p className="text-xs text-slate-500">{formatRetryLine(item.retryCount)}</p>
                  {item.sourceChunk && (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs font-medium text-slate-600">Source chunk</summary>
                      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">{item.sourceChunk}</pre>
                    </details>
                  )}
                  {Boolean(item.validationDetails) && (
                    <ul className="mt-2 text-xs text-slate-600">
                      {formatValidationBreakdown(
                        parseValidationDetails(item.validationDetails),
                        parseValidationJustifications(item.validationJustifications),
                      ).map((line, li) => (
                        <li key={li}>{line}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          ))}
          {!savedDataset && (
            <button type="button" className="btn-primary" onClick={() => void handleSaveDirect()}>
              Save Dataset to Database
            </button>
          )}
        </div>
      )}
    </div>
  );
}
