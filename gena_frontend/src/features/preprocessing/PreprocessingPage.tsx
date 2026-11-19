import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  listDatasets,
  createDataset,
  createQueue,
  listQueues,
  addQueueTasks,
  patchDatasetMetadata,
  listDatasetTasks,
  listDatasetChunks,
  uploadDatasetDocument,
  deleteDataset,
} from '@/shared/api/dataset';
import { useSelectableModels } from '@/shared/api/useSelectableModels';
import { pickModelId } from '@/shared/lib/modelAvailability';
import { ModelSelect } from '@/shared/ui/ModelSelect';
import type { ChunkResponse, ChunkingMethod, Dataset, DocumentKind, GateResult, ChunkDetailed } from '@/shared/types/documents';
import {
  CHUNKING_METHOD_HINT_KEYS,
  CHUNKING_METHOD_KEYS,
  chunkingMethodsForKind,
  effectiveChunkingMethod,
  defaultChunkingMethodFor,
} from '@/shared/lib/chunkingMethods';
import {
  buildChunkResponseFromStored,
  chunkPreviewText,
  extractChunks,
  questionTypesToGenerate,
  storedQuestionTypes,
} from '@/shared/lib/normalizeChunk';
import { disabledGateResult } from '@/shared/lib/gateDecision';
import { anyActiveGeneration } from '@/shared/lib/progress';
import { queueNameSlug } from '@/shared/lib/queueName';
import { formatTimestamp, timestampMs } from '@/shared/lib/dateTime';
import { datasetStatusLabel, questionTypeLabel } from '@/shared/lib/labels';
import { usePagedList } from '@/shared/lib/usePagedList';
import { useSort } from '@/shared/lib/useSort';
import { useResizableColumns } from '@/shared/lib/useResizableColumns';
import { PageHeader } from '@/shared/ui/PageHeader';
import { Pagination } from '@/shared/ui/Pagination';
import { SortableTh } from '@/shared/ui/SortableTh';
import { StatusBanner, LoadingState } from '@/shared/ui/StatusBanner';
import { usePageTitle } from '@/shared/i18n/usePageTitle';
import { isAppLocale, suggestDatasetName, type AppLocale } from '@/shared/i18n';
import { isExpert, useAuth } from '@/features/auth/authStore';
import { AUTH_ENABLED } from '@/shared/config/auth';

const PIPELINE_MODE_KEYS = ['full', 'generator_validator_gate', 'generator_validator'] as const;

const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const QUESTION_TYPES = ['one', 'multi', 'open'] as const;

function pipelineGateEnabled(mode: string) {
  return mode !== 'generator_validator';
}

function pipelineRefineEnabled(mode: string) {
  return mode === 'full';
}

export function PreprocessingPage() {
  const { t, i18n } = useTranslation(['create', 'common', 'errors', 'nav']);
  usePageTitle('create', 'nav');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const auth = useAuth();
  const canManageDatasets = !AUTH_ENABLED || isExpert(auth.role);
  const locale: AppLocale = isAppLocale(i18n.language) ? i18n.language : 'en';

  const [selectedDsId, setSelectedDsId] = useState('');
  // Mirrors selectedDsId for async callbacks: a chunk load that finishes after
  // the user picked another dataset must not overwrite the newer selection.
  const selectedDsIdRef = useRef(selectedDsId);
  selectedDsIdRef.current = selectedDsId;
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [chunkData, setChunkData] = useState<ChunkResponse | null>(null);
  const [documentName, setDocumentName] = useState('');
  const [datasetName, setDatasetName] = useState('');
  const [datasetDescription, setDatasetDescription] = useState('');
  const [questionTypes, setQuestionTypes] = useState<string[]>([...QUESTION_TYPES]);
  const [genModelId, setGenModelId] = useState('');
  const [valModelId, setValModelId] = useState('');
  const [pipelineMode, setPipelineMode] = useState('full');
  const [ablationTesting, setAblationTesting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const generatingRef = useRef(false);
  const [gateSummary, setGateSummary] = useState<string | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  // The dataset name we launched during this session, so we stop nagging about
  // a "duplicate" name that we ourselves just created a moment ago.
  const [lastRunName, setLastRunName] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [showChunks, setShowChunks] = useState(false);
  const [dsSearch, setDsSearch] = useState('');
  const [lastEnqueuedDsId, setLastEnqueuedDsId] = useState<string | null>(null);
  const [documentKind, setDocumentKind] = useState<DocumentKind>('normative');
  const [chunkingMethod, setChunkingMethod] = useState<ChunkingMethod>(defaultChunkingMethodFor('normative'));
  // Until the user picks a method by hand, it follows the document kind.
  const [methodTouched, setMethodTouched] = useState(false);
  const chunkingMethodUsed = effectiveChunkingMethod(documentKind, chunkingMethod);
  useEffect(() => {
    if (!methodTouched) setChunkingMethod(defaultChunkingMethodFor(documentKind));
  }, [documentKind, methodTouched]);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: datasets = [], refetch: refetchDatasets, isLoading: loadingDatasets } = useQuery({
    queryKey: ['datasets'],
    queryFn: () => listDatasets(),
  });

  const resetGenerationState = () => {
    setLastRunName(null);
    setStatusMsg(null);
    setLastEnqueuedDsId(null);
    setErrorMsg(null);
    setGateSummary(null);
    setChunkData(null);
    setDocumentName('');
  };

  // Only models that answer the health probe; a choice that went down falls back to the first one.
  const { models } = useSelectableModels();
  const effectiveGenModelId = pickModelId(models, genModelId);
  const effectiveValModelId = pickModelId(models, valModelId);

  // Poll datasets for in-progress work; surfaced as a non-blocking notice only.
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

  const dsSort = useSort<Dataset>({ key: 'created', dir: 'desc' });
  const dsCols = useResizableColumns({ name: 260, source: 220, id: 220, actions: 160 });
  const dsSorted = dsSort.sortItems(datasets, {
    name: (d) => d.name,
    source: (d) => d.source_document,
    chunks: (d) => d.chunks_valid ?? 0,
    questions: (d) => d.questions_count ?? d.metadata?.total_questions_generated ?? 0,
    status: (d) => d.metadata?.status,
    created: (d) => timestampMs(d.created_at),
    id: (d) => d._id,
  });
  const dsPaged = usePagedList(dsSorted, {
    pageSize: 5,
    search: dsSearch,
    searchFields: (d) => [d.name, d.source_document, d.metadata?.status, d._id],
  });

  const loadExistingChunks = useMutation({
    mutationFn: async (ds: Dataset) => {
      const stored = await listDatasetChunks(ds._id, false);
      return buildChunkResponseFromStored(stored);
    },
    onSuccess: (data, ds) => {
      if (ds._id !== selectedDsIdRef.current) return;
      const docName = ds.source_document ?? ds.name;
      setChunkData(data);
      setDocumentName(docName);
      setDatasetName(ds.name);
      setDatasetDescription(ds.description ?? '');
      if (!data.num_chunks) {
        setErrorMsg(t('create:noStoredChunks', { name: ds.name }));
      } else {
        setErrorMsg(null);
      }
    },
    onError: (e, ds) => {
      if (ds._id !== selectedDsIdRef.current) return;
      setChunkData(null);
      setDocumentName('');
      setErrorMsg(e instanceof Error ? e.message : t('create:loadChunksFailed'));
    },
  });

  const deleteDatasetMutation = useMutation({
    mutationFn: (ds: Dataset) => deleteDataset(ds._id),
    onSuccess: (_data, ds) => {
      if (selectedDsId === ds._id) {
        setSelectedDsId('');
        resetGenerationState();
      }
      setErrorMsg(null);
      void refetchDatasets();
    },
    onError: (e) => {
      setErrorMsg(e instanceof Error ? e.message : t('create:deleteFailed'));
    },
  });

  const handleDeleteDataset = (ds: Dataset) => {
    if (deleteDatasetMutation.isPending) return;
    if (!window.confirm(t('create:deleteConfirm', { name: ds.name }))) return;
    deleteDatasetMutation.mutate(ds);
  };

  const handleClearUpload = () => {
    setUploadFile(null);
    setDocumentName('');
    setDatasetName('');
    setDatasetDescription('');
    setErrorMsg(null);
    resetGenerationState();
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleFileUpload = (file: File) => {
    setUploadFile(file);
    setErrorMsg(null);
    resetGenerationState();
    setSelectedDsId('');
    if (file.size > MAX_UPLOAD_BYTES) {
      setErrorMsg(t('create:fileTooLarge'));
      setUploadFile(null);
      return;
    }
    // Chunking no longer runs on upload — it happens asynchronously in the queue
    // after the user clicks "Generate". We just capture the file and pre-fill the
    // dataset name/description so settings can be picked immediately.
    const docName = file.name.replace(/\.[^.]+$/, '');
    setDocumentName(docName);
    setDatasetName(suggestDatasetName(file.name, locale, t('common:documentFallback')));
    setDatasetDescription(t('create:generatedFrom', { file: file.name }));
  };

  const handleGenerate = async () => {
    if (generatingRef.current) return;
    if (!questionTypes.length) return;
    // Need either a fresh upload (chunked later in the queue) or already-loaded
    // chunk data (existing dataset).
    if (!uploadFile && !chunkData) return;
    const trimmedName = datasetName.trim();
    if (!trimmedName) {
      setErrorMsg(t('create:nameRequired'));
      return;
    }
    if (duplicateName) {
      setErrorMsg(t('create:duplicateName', { name: effectiveRunName }));
      return;
    }
    setErrorMsg(null);
    setStatusMsg(null);
    setLastEnqueuedDsId(null);
    setGateSummary(null);
    setGenerating(true);
    generatingRef.current = true;

    try {
      // Running generations no longer block a new run — the queue processes
      // everything in the background, so concurrent runs are allowed. Any
      // in-progress work is surfaced as a non-blocking notice instead.
      const gateEnabled = pipelineGateEnabled(pipelineMode);
      const datasetRunName =
        ablationTesting && !usingExisting ? `${datasetName}__${pipelineMode}` : datasetName;

      // Fresh upload: chunking itself runs in the queue. Create the dataset,
      // upload the raw file, enqueue a single "chunk" task, and send the user
      // straight to the queue screen — no waiting for chunking here. The worker
      // downloads the document, chunks it, then fans out gate/generation.
      if (!usingExisting) {
        if (!uploadFile) {
          setErrorMsg(t('create:uploadFirst'));
          setGenerating(false);
          return;
        }
        const ts = new Date().toISOString().replace(/\D/g, '').slice(0, 14);
        const queueNameNew = `queue_${queueNameSlug(datasetRunName)}_${ts}`;
        const sourceName = documentName || uploadFile.name;
        await createQueue({
          name: queueNameNew,
          description: t('create:queueFor', { name: datasetRunName }),
          priority: 1,
        });
        const created = await createDataset({
          name: datasetRunName,
          description: datasetDescription,
          source_document: sourceName,
          questions: [],
          metadata: {
            ablation_testing: ablationTesting,
            queue_name: queueNameNew,
            pipeline_mode: pipelineMode,
            gate_enabled: gateEnabled,
            gate_async: true,
            chunking_pending: true,
            refine_enabled: pipelineRefineEnabled(pipelineMode),
            experiment_source_document: sourceName,
            question_types: questionTypes,
            document_kind: documentKind,
            chunking_method: chunkingMethodUsed,
            num_chunks_processed: 0,
            total_chunks: 0,
            chunks_passed_gate: 0,
            chunks_rejected: 0,
            total_questions_generated: 0,
            questions_per_chunk: questionTypes.length,
            expected_questions: 0,
            generation_model_id: effectiveGenModelId || undefined,
            validation_model_id: effectiveValModelId || undefined,
            created_at: new Date().toISOString(),
            status: 'processing',
          },
        });
        const dsId = created.dataset_id;
        await uploadDatasetDocument(dsId, uploadFile);
        await addQueueTasks(queueNameNew, [{
          chunk_id: 0,
          chunk_text: '',
          question_type: questionTypes.includes('multi') ? 'multi' : questionTypes[0],
          question_types: questionTypes,
          task_type: 'chunk' as const,
          source_document: sourceName,
          dataset_name: datasetRunName,
          dataset_id: dsId,
          dataset_description: datasetDescription,
          // Этапы разбираются по приоритету: разбивка (9) → отбор (5) → генерация (1).
          priority: 9,
          pipeline_mode: pipelineMode,
          document_kind: documentKind,
          chunking_method: chunkingMethodUsed,
          ...(effectiveGenModelId ? { generation_model_id: effectiveGenModelId } : {}),
          ...(effectiveValModelId ? { validation_model_id: effectiveValModelId } : {}),
        }]);
        setLastRunName(datasetRunName);
        setLastEnqueuedDsId(dsId);
        refetchDatasets();
        // Prime the queues cache so the overview screen finds the freshly
        // created queue immediately instead of flashing "queue not found".
        await queryClient.fetchQuery({ queryKey: ['queues'], queryFn: listQueues }).catch(() => {});
        setGenerating(false);
        navigate(`/queue_overview?queue=${encodeURIComponent(queueNameNew)}`);
        return;
      }

      // Existing dataset: reuse stored chunks / gate verdicts and enqueue generation.
      const cd = chunkData;
      if (!cd || !cd.num_chunks) {
        setErrorMsg(t('create:zeroChunks'));
        setGenerating(false);
        return;
      }
      const extracted = extractChunks(cd.chunks_detailed, cd.chunks);

      if (!extracted.length) {
        setErrorMsg(t('create:allChunksEmpty'));
        setGenerating(false);
        return;
      }

      const ts = new Date().toISOString().replace(/\D/g, '').slice(0, 14);
      const queueNameNew = `queue_${queueNameSlug(datasetRunName)}_${ts}`;

      const gateResults: Record<number, GateResult> = {};
      let validChunks: Array<(typeof extracted)[0] & { typesOk: string[]; gate: GateResult }> = [];
      let rejected = 0;

      if (!gateEnabled) {
        validChunks = extracted.map((ec) => {
          const gate = disabledGateResult();
          gateResults[ec.idx] = gate;
          return { ...ec, typesOk: [...questionTypes], gate };
        });
        setGateSummary(t('create:gateDisabledSummary', { valid: validChunks.length, total: extracted.length }));
      } else {
        for (const ec of extracted) {
          const typesOk = questionTypesToGenerate(ec, questionTypes, true);
          if (typesOk.length) {
            const gate = (ec.gateResult as GateResult) ?? { passed: true };
            gateResults[ec.idx] = gate;
            validChunks.push({ ...ec, typesOk, gate });
          }
        }
        rejected = extracted.length - validChunks.length;
        setGateSummary(t('create:gatePassedSummary', {
          valid: validChunks.length,
          total: extracted.length,
          rejected,
        }));
      }

      if (!validChunks.length) {
        setErrorMsg(t('create:allChunksRejected'));
        setGenerating(false);
        return;
      }

      await createQueue({
        name: queueNameNew,
        description: t('create:queueFor', { name: datasetRunName }),
        priority: 1,
      });

      const dsId = chosenDs!._id;
      const totalTasksPlanned = validChunks.reduce((s, vc) => s + vc.typesOk.length, 0);
      await patchDatasetMetadata(dsId, {
        status: 'processing',
        queue_name: queueNameNew,
        pipeline_mode: pipelineMode,
        gate_enabled: gateEnabled,
        refine_enabled: pipelineRefineEnabled(pipelineMode),
        question_types: questionTypes,
        document_kind: documentKind,
        total_chunks: cd.num_chunks,
        chunks_passed_gate: validChunks.length,
        chunks_rejected: rejected,
        total_questions_generated: 0,
        expected_questions: totalTasksPlanned,
        generation_model_id: effectiveGenModelId || undefined,
        validation_model_id: effectiveValModelId || undefined,
        last_updated: new Date().toISOString(),
      });
      setStatusMsg(t('create:reusingDataset', { name: datasetName, id: dsId }));

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
          document_kind: documentKind,
          ...(storedQuestionTypes(vc, questionTypes).includes(qt)
            ? { chunk_pre_validated: true }
            : {}),
          pipeline_mode: pipelineMode,
          ...(effectiveGenModelId ? { generation_model_id: effectiveGenModelId } : {}),
          ...(effectiveValModelId ? { validation_model_id: effectiveValModelId } : {}),
        })),
      );

      await addQueueTasks(queueNameNew, tasks);
      setLastRunName(datasetRunName);
      setLastEnqueuedDsId(dsId);
      refetchDatasets();
      await queryClient.fetchQuery({ queryKey: ['queues'], queryFn: listQueues }).catch(() => {});
      setGenerating(false);
      navigate(`/queue_overview?queue=${encodeURIComponent(queueNameNew)}`);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : t('create:generationFailed'));
    } finally {
      generatingRef.current = false;
      setGenerating(false);
    }
  };

  const hasData = chunkData != null && (chunkData.num_chunks ?? 0) > 0;
  const previewItems = chunkData?.chunks_detailed ?? chunkData?.chunks ?? [];
  // A fresh upload is enough to configure + generate — chunking happens later in
  // the queue, so we no longer wait for chunk data before showing settings.
  const hasUpload = Boolean(uploadFile) && !usingExisting;
  const showConfig = hasUpload || hasData;

  if (loadingDatasets) return <LoadingState label={t('create:loadingDatasets')} />;

  const pipelineModeLabel = (mode: string) => {
    if (mode === 'full') return t('create:pipelineFull');
    if (mode === 'generator_validator_gate') return t('create:pipelineGate');
    return t('create:pipelineNoGate');
  };

  return (
    <div>
      <PageHeader
        title={t('create:title')}
        subtitle={t('create:subtitle')}
      />

      {usingExisting && chosenDs ? (
        <div className="space-y-4">
          <p className="rounded-lg bg-green-50 p-3 text-green-800">
            {t('create:usingExisting', { name: datasetName, source: documentName })}
          </p>
          {loadExistingChunks.isPending && <p className="text-sm text-slate-500">{t('create:loadingChunks')}</p>}
          <div>
            <label className="text-sm font-medium">{t('create:datasetName')}</label>
            <input className="input-field mt-1" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
          </div>
          <div>
            <label className="text-sm font-medium">{t('create:description')}</label>
            <textarea className="input-field mt-1" rows={2} value={datasetDescription} onChange={(e) => setDatasetDescription(e.target.value)} />
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-lg font-semibold">{t('create:uploadNew')}</h2>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                className="text-sm font-medium text-gena-primary hover:underline"
                onClick={() => {
                  void (async () => {
                    try {
                      const res = await fetch('/samples/example_document.txt');
                      if (!res.ok) throw new Error('sample missing');
                      const text = await res.text();
                      const file = new File([text], 'example_document.txt', { type: 'text/plain' });
                      handleFileUpload(file);
                    } catch {
                      setErrorMsg(t('create:chunkingFailed'));
                    }
                  })();
                }}
              >
                {t('create:tryExample')}
              </button>
              <button
                type="button"
                className="text-sm text-slate-600 hover:underline"
                onClick={() => {
                  const a = document.createElement('a');
                  a.href = '/samples/example_document.txt';
                  a.download = 'example_document.txt';
                  a.click();
                }}
              >
                {t('create:downloadExample')}
              </button>
            </div>
          </div>
          <div
            className={`relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors ${
              dragActive
                ? 'border-gena-primary bg-gena-primary/5'
                : 'border-slate-300 bg-white hover:border-gena-primary/60'
            }`}
            onDragEnter={(e) => { e.preventDefault(); e.stopPropagation(); setDragActive(true); }}
            onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragActive(true); }}
            onDragLeave={(e) => { e.preventDefault(); e.stopPropagation(); setDragActive(false); }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setDragActive(false);
              const f = e.dataTransfer.files?.[0];
              if (f) handleFileUpload(f);
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".docx,.txt,.pdf"
              className="absolute inset-0 cursor-pointer opacity-0"
              aria-label={t('create:chooseFile')}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFileUpload(f);
              }}
            />
            <p className="text-sm font-medium text-slate-800">
              {dragActive ? t('create:dropzoneActive') : t('create:dropzoneHint')}
            </p>
            <p className="mt-1 text-xs text-slate-500">{t('create:dropzoneFormats')}</p>
            {uploadFile ? (
              <p className="relative z-10 mt-3 flex items-center justify-center gap-2 text-sm text-emerald-700">
                <span>{uploadFile.name}</span>
                <button
                  type="button"
                  className="rounded px-1.5 text-base leading-none text-slate-500 hover:bg-slate-100 hover:text-slate-800"
                  aria-label={t('create:removeFile')}
                  title={t('create:removeFile')}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    handleClearUpload();
                  }}
                >
                  ×
                </button>
              </p>
            ) : (
              <p className="mt-3 text-xs text-slate-400">{t('create:noFileChosen')}</p>
            )}
          </div>
          {uploadFile && <p className="text-green-700">{t('create:fileUploaded', { name: uploadFile.name })}</p>}
          {hasUpload && (
            <>
              <div>
                <label className="text-sm font-medium">{t('create:datasetName')}</label>
                <input className="input-field mt-1" value={datasetName} onChange={(e) => setDatasetName(e.target.value)} />
                {duplicateName && (
                  <div className="mt-2">
                    <StatusBanner tone="warning">
                      {t('create:duplicateName', { name: effectiveRunName })}
                    </StatusBanner>
                  </div>
                )}
              </div>
              <div>
                <label className="text-sm font-medium">{t('create:description')}</label>
                <textarea className="input-field mt-1" rows={2} value={datasetDescription} onChange={(e) => setDatasetDescription(e.target.value)} />
              </div>
            </>
          )}
        </div>
      )}

      {!hasUpload && (
        <>
          <hr className="my-6" />
          {datasets.length ? (
            <details className="rounded-xl border border-slate-200 bg-white p-4">
              <summary className="cursor-pointer font-semibold text-slate-800">
                {t('create:existingDatasets')}
              </summary>
              <div className="mt-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <input
                      type="search"
                      className="input-field max-w-xs"
                      placeholder={t('create:searchDatasets')}
                      value={dsSearch}
                      onChange={(e) => { setDsSearch(e.target.value); dsPaged.setPage(1); }}
                    />
                    <Pagination
                      page={dsPaged.page}
                      totalPages={dsPaged.totalPages}
                      total={dsPaged.total}
                      onPageChange={dsPaged.setPage}
                      countLabel={(n) => t('common:datasets', { count: n })}
                    />
                  </div>
                  <div className="mt-2 w-full overflow-x-auto rounded-xl border border-slate-200 bg-white">
                    <table className="data-table" ref={dsCols.tableRef} style={dsCols.tableStyle}>
                      <thead>
                        <tr>
                          <SortableTh label={t('create:colName')} sortKey="name" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} className="w-64" />
                          <SortableTh label={t('create:colSource')} sortKey="source" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                          <SortableTh label={t('create:colChunks')} sortKey="chunks" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                          <SortableTh label={t('create:colQuestions')} sortKey="questions" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                          <SortableTh label={t('create:colStatus')} sortKey="status" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                          <SortableTh label={t('create:colCreated')} sortKey="created" sort={dsSort.sort} onSort={dsSort.requestSort} resize={dsCols} />
                          {canManageDatasets && (
                            <th
                              data-col="actions"
                              className="text-left"
                              style={
                                dsCols.widthFor('actions') != null
                                  ? {
                                      width: dsCols.widthFor('actions'),
                                      maxWidth: dsCols.widthFor('actions'),
                                    }
                                  : undefined
                              }
                            >
                              {t('create:colActions')}
                            </th>
                          )}
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
                            <td>{ds.questions_count ?? ds.metadata?.total_questions_generated ?? 0}</td>
                            <td>{datasetStatusLabel(ds.metadata?.status, t)}</td>
                            <td className="whitespace-nowrap text-slate-500">{formatTimestamp(ds.created_at)}</td>
                            {canManageDatasets && (
                              <td className="whitespace-nowrap align-top">
                                <button
                                  type="button"
                                  className="text-xs font-medium text-red-700 hover:underline disabled:opacity-50"
                                  disabled={deleteDatasetMutation.isPending}
                                  onClick={() => handleDeleteDataset(ds)}
                                >
                                  {t('create:deleteDataset')}
                                </button>
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <label className="mt-4 block text-sm font-medium">{t('create:useExisting')}</label>
                  <select
                    className="input-field mt-1 max-w-xl"
                    value={selectedDsId}
                    onChange={(e) => {
                      const id = e.target.value;
                      setSelectedDsId(id);
                      setErrorMsg(null);
                      resetGenerationState();
                      setUploadFile(null);
                      if (fileInputRef.current) fileInputRef.current.value = '';
                      const ds = datasets.find((d) => d._id === id);
                      if (ds) {
                        const kind = ds.metadata?.document_kind;
                        if (kind === 'textbook' || kind === 'encyclopedia' || kind === 'normative') {
                          setDocumentKind(kind);
                        } else {
                          setDocumentKind('normative');
                        }
                        loadExistingChunks.mutate(ds);
                      } else {
                        setDocumentKind('normative');
                      }
                    }}
                  >
                    <option value="">{t('create:noneUploadNew')}</option>
                    {dsPaged.filteredItems.map((ds) => {
                      const chunkTotal = ds.chunks_count ?? ds.metadata?.total_chunks ?? 0;
                      const empty = chunkTotal === 0;
                      return (
                        <option key={ds._id} value={ds._id} disabled={empty}>
                          {ds.name} ({ds.source_document})
                          {empty ? ` ${t('create:noChunksSuffix')}` : ''}
                        </option>
                      );
                    })}
                  </select>
              </div>
            </details>
          ) : (
            <p className="mt-2 text-slate-600">{t('create:noDatasets')}</p>
          )}
        </>
      )}

      {showConfig && (
        <>
          {hasData && (
            <>
              <button type="button" className="mt-4 text-sm text-gena-primary" onClick={() => setShowChunks(!showChunks)}>
                {showChunks
                  ? t('create:hideChunks', { count: chunkData!.num_chunks })
                  : t('create:previewChunks', { count: chunkData!.num_chunks })}
              </button>
              {showChunks && (
                <div className="mt-2 max-h-96 space-y-2 overflow-y-auto">
                  {previewItems.slice(0, 50).map((ch, i) => {
                    const chunkIdx =
                      typeof ch === 'object' && ch !== null
                        ? (ch as ChunkDetailed)._chunk_index
                          ?? (() => {
                            const m = (ch as ChunkDetailed).fragment_id?.match(/_chunk_(\d+)$/);
                            return m ? Number(m[1]) : i + 1;
                          })()
                        : i + 1;
                    return (
                    <div key={chunkIdx} className="card text-xs">
                      <div className="font-medium">{t('create:chunkLabel', { n: chunkIdx })}</div>
                      <pre className="mt-1 whitespace-pre-wrap">{chunkPreviewText(ch as never)}</pre>
                    </div>
                    );
                  })}
                </div>
              )}
            </>
          )}

          <hr className="my-6" />
          <h3 className="font-semibold">{t('create:questionTypes')}</h3>
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
                {questionTypeLabel(qt)}
              </label>
            ))}
          </div>
          {questionTypes.length === 0 && (
            <p className="mt-2 text-sm text-amber-700">{t('create:selectTypeHint')}</p>
          )}

          <div className="mt-6">
            <label className="text-sm font-medium">{t('create:documentKind')}</label>
            <select
              className="input-field mt-1 max-w-xl"
              value={documentKind}
              onChange={(e) => setDocumentKind(e.target.value as DocumentKind)}
            >
              <option value="normative">{t('create:kindNormative')}</option>
              <option value="textbook">{t('create:kindTextbook')}</option>
              <option value="encyclopedia">{t('create:kindEncyclopedia')}</option>
            </select>
          </div>

          <div className="mt-4">
            <label className="text-sm font-medium">{t('create:chunkingMethod')}</label>
            <select
              className="input-field mt-1 max-w-xl"
              value={chunkingMethodUsed}
              disabled={chunkingMethodsForKind(documentKind).length === 1}
              onChange={(e) => {
                setMethodTouched(true);
                setChunkingMethod(e.target.value as ChunkingMethod);
              }}
            >
              {chunkingMethodsForKind(documentKind).map((m) => (
                <option key={m} value={m}>{t(CHUNKING_METHOD_KEYS[m])}</option>
              ))}
            </select>
            <p className="mt-1 text-xs text-slate-500">
              {chunkingMethodsForKind(documentKind).length === 1
                ? t('create:methodOnlyRulesForNormative')
                : t(CHUNKING_METHOD_HINT_KEYS[chunkingMethodUsed])}
            </p>
          </div>

          <details className="mt-6 rounded-xl border border-slate-200 bg-white p-4">
            <summary className="cursor-pointer font-semibold text-slate-800">
              {t('create:advancedSettings')}
            </summary>
            <div className="mt-4 space-y-4">
              {models.length > 0 && (
                <div>
                  <h3 className="font-medium">{t('create:modelSelection')}</h3>
                  <div className="mt-2 grid gap-4 sm:grid-cols-2">
                    <div>
                      <label className="text-sm">{t('create:generationModel')}</label>
                      <ModelSelect models={models} value={effectiveGenModelId} onChange={setGenModelId} />
                    </div>
                    <div>
                      <label className="text-sm">{t('create:validationModel')}</label>
                      <ModelSelect models={models} value={effectiveValModelId} onChange={setValModelId} />
                    </div>
                  </div>
                </div>
              )}

              <div>
                <label className="text-sm font-medium">{t('create:pipelineMode')}</label>
                <select className="input-field mt-1 max-w-xl" value={pipelineMode} onChange={(e) => setPipelineMode(e.target.value)}>
                  {PIPELINE_MODE_KEYS.map((k) => (
                    <option key={k} value={k}>{pipelineModeLabel(k)}</option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-slate-500">
                  {t('create:gateEnabled', {
                    state: pipelineGateEnabled(pipelineMode) ? t('create:enabled') : t('create:disabled'),
                  })}
                  ;{' '}
                  {t('create:refineEnabled', {
                    state: pipelineRefineEnabled(pipelineMode) ? t('create:enabled') : t('create:disabled'),
                  })}
                  .
                </p>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={ablationTesting} onChange={(e) => setAblationTesting(e.target.checked)} />
                {t('create:ablation')}
              </label>
            </div>
          </details>

          {activeGen.active && (
            <div className="mt-6">
              <StatusBanner tone="info">
                {t('create:activeGeneration', {
                  queue: activeGen.queueName
                    ? t('create:activeQueue', { name: activeGen.queueName })
                    : '',
                })}{' '}
                <Link to="/queue_manager" className="underline">{t('create:openProgress')}</Link>
              </StatusBanner>
            </div>
          )}

          <button
            type="button"
            className="btn-primary mt-6"
            disabled={
              !questionTypes.length ||
              generating ||
              duplicateName ||
              !datasetName.trim() ||
              loadExistingChunks.isPending ||
              (usingExisting && (!chunkData || !(chunkData.num_chunks ?? 0)))
            }
            onClick={() => void handleGenerate()}
          >
            {generating ? t('create:generating') : t('create:generate')}
          </button>
        </>
      )}

      {gateSummary && <div className="mt-4"><StatusBanner tone="info">{gateSummary}</StatusBanner></div>}
      {statusMsg && <div className="mt-4"><StatusBanner tone="success">{statusMsg}</StatusBanner></div>}
      {lastEnqueuedDsId && (
        <div className="mt-2 flex flex-wrap gap-4">
          <Link
            to={`/dataset_editor?dataset=${lastEnqueuedDsId}`}
            className="text-gena-primary hover:underline"
          >
            {t('create:openResults')}
          </Link>
          <Link
            to="/queue_manager"
            className="text-gena-primary hover:underline"
          >
            {t('create:openProgress')}
          </Link>
        </div>
      )}
      {errorMsg && <div className="mt-4"><StatusBanner tone="error">{errorMsg}</StatusBanner></div>}
    </div>
  );
}
