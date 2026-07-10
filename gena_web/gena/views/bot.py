import streamlit as st
import os
import requests
import pandas as pd
import tempfile
from collections import Counter
from datetime import datetime

from gena.config import API_CHANKS_URL, CHUNKS_DIR, API_GEN_QUE_URL, API_DATASET_URL, DOCS_DIR, AGENT_API_URL
from gena.http import get, post, put, delete
from gena.views import page_subtitle
from gena.validation_display import (
    format_retry_line,
    format_validation_breakdown_md,
)

# ── Session state defaults ──
_DEFAULTS = {
    "chunk_data": None,
    "chunk_file_id": None,
    "document_name": None,
    "results": None,
    "dataset_saved": False,
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


PIPELINE_MODE_OPTIONS = {
    "generator_validator": "Generator + validator (no gate, no refine)",
    "generator_validator_gate": "Generator + validator + gate (no refine)",
    "full": "Full pipeline (gate + refine)",
}


def _pipeline_gate_enabled(pipeline_mode: str) -> bool:
    return pipeline_mode != "generator_validator"


def _pipeline_refine_enabled(pipeline_mode: str) -> bool:
    return pipeline_mode == "full"


@st.cache_data(ttl=120)
def fetch_available_models():
    """Загружает список доступных моделей из agent_api."""
    try:
        resp = requests.get(f"{AGENT_API_URL}/models/", timeout=30)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []


@st.cache_data(ttl=60)
def fetch_models_health():
    """Быстрая проверка доступности моделей (из кэша последнего probe)."""
    try:
        resp = requests.get(
            f"{AGENT_API_URL}/models/health/", timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []


def _any_active_generation(datasets: list):
    """Return ``(active, queue_name)``: True if any dataset is still ``processing`` with
    pending/processing tasks; ``queue_name`` is taken from the dataset metadata
    (or from a task as a fallback) so callers can surface it in messages."""
    for ds in datasets or []:
        meta = ds.get("metadata") or {}
        if meta.get("status") != "processing":
            continue
        ds_id = ds.get("_id", "")
        if not ds_id:
            continue
        try:
            resp = get(f"/datasets/{ds_id}/tasks")
            if resp.status_code != 200:
                continue
            tasks = resp.json() or []
        except Exception:
            continue
        for t in tasks:
            if t.get("status") in ("pending", "processing"):
                qname = meta.get("queue_name") or t.get("queue_name") or ""
                return True, qname
    return False, ""


def chunk_document(uploaded_file):
    """Отправляет файл на chunker и возвращает результат. Кеширует по file_id."""
    file_id = f"{uploaded_file.name}_{uploaded_file.size}"
    if st.session_state.chunk_file_id == file_id and st.session_state.chunk_data is not None:
        return st.session_state.chunk_data, st.session_state.document_name

    with tempfile.NamedTemporaryFile(delete=False, suffix=uploaded_file.name.split('.')[-1]) as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as f:
            files = {"file": (uploaded_file.name, f, uploaded_file.type)}
            response = requests.post(API_CHANKS_URL, files=files)
    except requests.exceptions.ConnectionError:
        st.error("Could not connect to the chunker server. Make sure it is running.")
        st.stop()
    finally:
        os.remove(tmp_path)

    if response.status_code != 200:
        st.error(f"Server error: {response.status_code}")
        st.json(response.text)
        st.session_state.chunk_data = None
        st.session_state.chunk_file_id = None
        st.session_state.document_name = uploaded_file.name
        return None, uploaded_file.name

    data = response.json()
    document_name = None
    if "document_type" in data and isinstance(data["document_type"], dict):
        document_name = data["document_type"].get("document_name")
    if not document_name:
        document_name = uploaded_file.name

    st.session_state.chunk_data = data
    st.session_state.chunk_file_id = file_id
    st.session_state.document_name = document_name
    return data, document_name


# ── Header ──

if CHUNKS_DIR:
    os.makedirs(CHUNKS_DIR, exist_ok=True)

st.title("Data Preprocessing")

page_subtitle(
    "Select an existing dataset or upload a new document, choose question types, "
    "and generate a dataset. Results will appear in <strong>Results &amp; Editor</strong>."
)
col_nav1, col_nav2, _spacer = st.columns([1, 1, 4])
with col_nav1:
    st.page_link("views/home.py", label="Home", icon=":material/home:")
with col_nav2:
    st.page_link("views/docs.py", label="Documentation", icon=":material/menu_book:")

st.markdown("---")

# ── Existing Datasets ──

st.markdown("## Existing Datasets")
st.caption("Select a previously uploaded dataset or upload a new document below.")

_ds_list = []
try:
    _ds_resp = get("/datasets/")
    if _ds_resp.status_code == 200:
        _ds_list = _ds_resp.json() or []
except Exception:
    pass

if _ds_list:
    _ds_rows = []
    for _ds in _ds_list:
        _meta = _ds.get("metadata") or {}
        _stored_ch = _ds.get("chunks_count", 0)
        _meta_ch = _meta.get("total_chunks")
        _total_ch = _stored_ch if _stored_ch else (_meta_ch if _meta_ch is not None else 0)
        _ds_rows.append({
            "Name": _ds.get("name", ""),
            "Source": _ds.get("source_document", ""),
            "Chunks (valid/total)": f"{_ds.get('chunks_valid', 0)}/{_total_ch}",
            "Questions": len(_ds.get("questions", [])),
            "Status": _meta.get("status", ""),
            "Created": str(_ds.get("created_at", ""))[:19],
            "ID": _ds.get("_id", ""),
        })
    st.dataframe(pd.DataFrame(_ds_rows), use_container_width=True, hide_index=True)

    _ds_names = ["— none (upload new) —"] + [
        f"{d.get('name', '')} ({d.get('source_document', '')})" for d in _ds_list
    ]
    _selected_ds_idx = st.selectbox(
        "Use an existing dataset:",
        range(len(_ds_names)),
        format_func=lambda i: _ds_names[i],
        key="selected_ds_idx",
    )
else:
    st.info("No datasets found. Upload a document below to create one.")
    _selected_ds_idx = 0

_using_existing = _selected_ds_idx > 0 and _selected_ds_idx <= len(_ds_list)

st.markdown("---")

# ── Source: existing dataset OR new upload ──

data = None
document_name = None
dataset_name = ""
dataset_description = ""

if _using_existing:
    _chosen_ds = _ds_list[_selected_ds_idx - 1]
    _chosen_id = _chosen_ds.get("_id", "")
    document_name = _chosen_ds.get("source_document", _chosen_ds.get("name", ""))
    dataset_name = _chosen_ds.get("name", "")
    dataset_description = _chosen_ds.get("description", "")

    st.success(f"Using existing dataset: **{dataset_name}** (source: {document_name})")

    # Load chunks from the chunks collection (include gate-rejected for re-runs).
    try:
        _chunks_resp = get(
            f"/datasets/{_chosen_id}/chunks",
            params={"gate_passed_only": False},
        )
        if _chunks_resp.status_code == 200:
            _stored_chunks = _chunks_resp.json()
            _chunk_list_detailed = []
            _chunk_list_text = []
            for _sc in _stored_chunks:
                _fd = _sc.get("fragment_data") or {}
                _ct = _sc.get("chunk_text", "")
                _combined = _fd.get("combined_text", _ct)
                _chunk_list_text.append(_combined)
                _chunk_list_detailed.append({
                    "fragment_id": f"stored_chunk_{_sc.get('chunk_index', 0)}",
                    "fragment_data": {
                        **_fd,
                        "combined_text": _combined,
                        "content": _fd.get("content", _ct),
                        "title": _fd.get("title", ""),
                    },
                    "hierarchy_context": _fd.get("hierarchy_context", {}),
                    "_gate_result": _sc.get("gate_result"),
                    "_gate_passed": _sc.get("gate_passed", True),
                    "_question_types_valid": _sc.get("question_types_valid"),
                })

            data = {
                "num_chunks": len(_chunk_list_detailed),
                "chunks": _chunk_list_text,
                "chunks_detailed": _chunk_list_detailed,
                "chunking_method": "stored",
            }
            if data["num_chunks"] > 0:
                st.info(f"Loaded {data['num_chunks']} chunks from dataset")
            else:
                _meta_total = (_chosen_ds.get("metadata") or {}).get("total_chunks")
                if _meta_total:
                    st.error(
                        f"This dataset has no stored chunks (metadata says {_meta_total} were "
                        "processed). It was likely created before chunk storage or the run did "
                        "not finish. Upload the source document again or pick a dataset with "
                        "stored chunks."
                    )
                else:
                    st.warning("Loaded 0 chunks from dataset. Upload the source document to continue.")
        else:
            st.warning(f"Could not load chunks (HTTP {_chunks_resp.status_code}). You can still upload a new file.")
    except Exception as _e:
        st.warning(f"Error loading chunks: {_e}")

    if data and data["num_chunks"] > 0:
        with st.expander("Preview chunks", expanded=False):
            for ci, ch in enumerate(data["chunks_detailed"][:50], 1):
                _fd = ch.get("fragment_data", {})
                txt = _fd.get("combined_text", "")
                h = max(100, min(400, len(txt) // 5))
                st.text_area(f"Chunk {ci} ({len(txt)} chars)", txt, height=h, disabled=True, key=f"existing_chunk_{ci}")
            if data["num_chunks"] > 50:
                st.caption(f"Showing first 50 of {data['num_chunks']} chunks.")

    dataset_name = st.text_input(
        "Dataset Name:", value=dataset_name, key=f"ds_name_existing_{_chosen_id}"
    )
    dataset_description = st.text_area(
        "Dataset Description (optional):",
        value=dataset_description,
        key=f"ds_desc_existing_{_chosen_id}",
    )

else:
    st.markdown("## Upload a new document")
    col_upload, col_example = st.columns([3, 1])
    with col_example:
        example_path = os.path.join(DOCS_DIR, "Family_code_Russian_Federation_1-4.docx")
        if os.path.exists(example_path):
            with open(example_path, "rb") as f:
                st.download_button(
                    label="Download example document",
                    data=f,
                    file_name="Family_code_Russian_Federation_1-4.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="template_download",
                )
    with col_upload:
        uploaded_file = st.file_uploader("Upload a document", type=['docx', 'txt', 'pdf'])

    if uploaded_file is not None:
        st.success(f"File uploaded: {uploaded_file.name}")

        _base_name = uploaded_file.name.rsplit(".", 1)[0]
        dataset_name = st.text_input(
            "Dataset Name:",
            value=_base_name,
        )

        _name_taken = any(d.get("name") == dataset_name for d in _ds_list)
        if _name_taken:
            st.warning(
                f"Dataset **{dataset_name}** already exists. "
                "Change the name or it will create a duplicate."
            )

        dataset_description = st.text_area("Dataset Description (optional):", value=f"Generated from {uploaded_file.name}")
        st.markdown("---")

        if not API_CHANKS_URL:
            st.error("API_CHANKS_URL is not configured. Please check your environment variables.")
            st.stop()

        st.markdown("### Chunking")
        _chunk_slot = st.empty()
        _chunk_bar = _chunk_slot.progress(0.05, text="Splitting document into chunks…")
        data, document_name = chunk_document(uploaded_file)

        if data is not None:
            _chunk_bar.progress(
                1.0,
                text=f"Document successfully split into chunks. Number of chunks: {data['num_chunks']}",
            )
            with st.expander("Preview chunks", expanded=False):
                for ci, ch in enumerate(data.get("chunks_detailed", data.get("chunks", []))[:50], 1):
                    if isinstance(ch, dict):
                        txt = (ch.get("fragment_data", {}).get("combined_text", "")
                               or ch.get("fragment_data", {}).get("content", "")
                               or str(ch))
                    else:
                        txt = str(ch)
                    h = max(100, min(400, len(txt) // 5))
                    st.text_area(f"Chunk {ci} ({len(txt)} chars)", txt, height=h, disabled=True, key=f"chunk_preview_{ci}")
                if data['num_chunks'] > 50:
                    st.caption(f"Showing first 50 of {data['num_chunks']} chunks.")
        else:
            _chunk_slot.empty()
            st.stop()

# ── Common generation flow (works for both existing dataset and new upload) ──

_has_data = data is not None and data.get("num_chunks", 0) > 0
_chunks_pre_validated = _using_existing

if _has_data:
    st.markdown("---")
    st.markdown("### Question Types Selection")

    question_types = st.multiselect(
        "Select question types to generate:",
        ['one', 'multi', 'open'],
        default=['one', 'multi', 'open'],
        key="question_types_select",
    )
    if not question_types:
        st.info("Select at least one question type to enable generation.")

    # ── Model Selection ──
    available_models = fetch_available_models()
    model_options = {m["id"]: m["name"] for m in available_models}

    health_data = fetch_models_health()
    health_map = {h["id"]: h["available"] for h in health_data}
    unavailable_models = [h for h in health_data if not h["available"]]

    if model_options:
        st.markdown("---")
        st.markdown("### Model Selection")

        if unavailable_models:
            names = ", ".join(h["name"] for h in unavailable_models)
            st.warning(
                f"**Models unavailable:** {names}. "
                "These models are not responding and tasks using them will fail."
            )

        def _model_label(model_id: str) -> str:
            name = model_options.get(model_id, model_id)
            if not health_map.get(model_id, True):
                return f"\u26a0\ufe0f {name} (unavailable)"
            return name

        col_gen, col_val = st.columns(2)

        gen_model_keys = list(model_options.keys())
        val_model_keys = list(model_options.keys())

        with col_gen:
            generation_model_id = st.selectbox(
                "Generation model:",
                gen_model_keys,
                format_func=_model_label,
                index=0,
                help="LLM for generating questions and for sensitivity / difficulty scores",
            )
            if not health_map.get(generation_model_id, True):
                st.error(
                    f"**{model_options[generation_model_id]}** is not responding. "
                    "Tasks sent with this model will likely fail."
                )

        with col_val:
            validation_model_id = st.selectbox(
                "Validation model:",
                val_model_keys,
                format_func=_model_label,
                index=0,
                help="LLM for chunk gate (suitability before generation) and post-generation validation (weighted score)",
            )
            if not health_map.get(validation_model_id, True):
                st.error(
                    f"**{model_options[validation_model_id]}** is not responding. "
                    "Tasks sent with this model will likely fail."
                )

        st.markdown("---")
    else:
        generation_model_id = None
        validation_model_id = None

    processing_mode = st.radio(
        "Processing Mode:",
        ["Queue Mode (Recommended)", "Direct Processing"],
        help="Queue Mode: Add tasks to queue for background processing. Direct Processing: Process immediately (may be slower).",
    )

    pipeline_mode = st.selectbox(
        "Pipeline Mode:",
        list(PIPELINE_MODE_OPTIONS.keys()),
        format_func=lambda m: PIPELINE_MODE_OPTIONS[m],
        index=2,
        help="Use non-full modes for ablation experiments. Full pipeline is the default production mode.",
    )
    gate_enabled = _pipeline_gate_enabled(pipeline_mode)
    refine_enabled = _pipeline_refine_enabled(pipeline_mode)
    st.caption(
        f"Gate: {'enabled' if gate_enabled else 'disabled'}; "
        f"refine: {'enabled' if refine_enabled else 'disabled'}."
    )
    ablation_testing = st.checkbox(
        "Ablation testing run",
        value=False,
        help=(
            "Tag this run as an ablation experiment. Upload the same document "
            "and run each pipeline mode to compare validator pass/fail rates."
        ),
    )
    if ablation_testing:
        st.info(
            "Ablation run: new datasets will be tagged with the selected "
            "pipeline mode. For a full study, run this document once per mode."
        )

    dataset_run_name = (
        f"{dataset_name}__{pipeline_mode}"
        if ablation_testing and not _using_existing
        else dataset_name
    )

    _active_gen, _active_queue_name = _any_active_generation(_ds_list)
    if _active_gen:
        if _active_queue_name:
            st.warning(
                "Another generation is in progress. Please wait for it to complete "
                "before starting a new one. "
                f"Processing queue: {_active_queue_name}. "
                "Switch to Queue Manager to monitor and manage queues."
            )
        else:
            st.warning(
                "Another generation is in progress. Please wait for it to complete "
                "before starting a new one. "
                "Switch to Queue Manager to monitor and manage queues."
            )
        st.page_link(
            "views/queue_manager.py",
            label="Open Queue Manager",
            icon=":material/arrow_forward:",
        )

    # ── Generate ──

    if st.button("Generate Questions", disabled=not question_types or _active_gen):
        chunks_detailed = data.get("chunks_detailed", [])
        chunks_text = data.get("chunks", [])
        total_chunks = data.get("num_chunks", 0)

        if total_chunks == 0:
            st.error("No chunks found.")
            st.stop()

        _leave_warn = st.empty()
        _leave_warn.warning("Please note: Your progress will be lost if you leave this page.")

        results = []

        if processing_mode == "Queue Mode (Recommended)":
            queue_name = f"queue_{dataset_run_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            try:
                # ── 1. Extract chunk texts ──
                extracted = []
                source_items = chunks_detailed if chunks_detailed else chunks_text
                for idx, chunk in enumerate(source_items, 1):
                    if isinstance(chunk, dict):
                        fd = chunk.get("fragment_data", {})
                        chunk_text = (fd.get("combined_text", "")
                                      or fd.get("content", "")
                                      or fd.get("title", "")
                                      or str(chunk))
                        fragment_data = fd if fd else None
                        gate_result_stored = chunk.get("_gate_result")
                        types_valid_stored = chunk.get("_question_types_valid")
                    else:
                        chunk_text = str(chunk)
                        fragment_data = None
                        gate_result_stored = None
                        types_valid_stored = None

                    if not chunk_text or len(chunk_text.strip()) < 10:
                        continue
                    extracted.append({
                        "idx": idx,
                        "text": chunk_text,
                        "fragment_data": fragment_data,
                        "_gate_result": gate_result_stored,
                        "_types_valid": types_valid_stored,
                    })

                if not extracted:
                    st.error("All chunks are empty or too short.")
                    st.stop()

                # ── 2. Gate validation (skip when disabled for ablation) ──
                gate_results = {}
                if not gate_enabled:
                    valid_chunks = []
                    for ec in extracted:
                        gate_result = {
                            "passed": True,
                            "rejection_reason": "gate_disabled_by_pipeline_mode",
                        }
                        gate_results[ec["idx"]] = gate_result
                        valid_chunks.append({
                            **ec,
                            "types_ok": list(question_types),
                            "gate": gate_result,
                        })
                    rejected = 0
                    st.info(
                        f"Gate disabled for pipeline mode '{pipeline_mode}'. "
                        f"Using all {len(valid_chunks)}/{len(extracted)} chunks."
                    )
                elif _chunks_pre_validated:
                    valid_chunks = []
                    for ec in extracted:
                        stored_types = ec.get("_types_valid") or list(question_types)
                        types_ok = [qt for qt in question_types if qt in stored_types]
                        if types_ok:
                            gate = ec.get("_gate_result") or {"passed": True, "rejection_reason": None}
                            gate_results[ec["idx"]] = gate
                            valid_chunks.append({
                                **ec, "types_ok": types_ok,
                                "gate": gate,
                            })
                    rejected = len(extracted) - len(valid_chunks)
                    st.info(
                        f"{len(valid_chunks)}/{len(extracted)} chunks passed gate, "
                        f"{rejected} rejected"
                    )
                else:
                    gate_url = f"{AGENT_API_URL}/chunk_gate/"
                    gate_qtype = "multi" if "multi" in question_types else question_types[0]

                    gate_bar = st.progress(0)
                    gate_status = st.empty()
                    gate_status.text("Running chunk gate validation...")

                    for gi, ec in enumerate(extracted):
                        gate_bar.progress((gi + 1) / len(extracted))
                        gate_status.text(f"Gate: chunk {ec['idx']} ({gi+1}/{len(extracted)})...")
                        try:
                            gr = requests.post(gate_url, json={
                                "chunk": ec["text"],
                                "question_type": gate_qtype,
                                **({"validation_model_id": validation_model_id} if validation_model_id else {}),
                            }, timeout=120)
                            if gr.status_code == 200:
                                gate_results[ec["idx"]] = gr.json().get("result", {})
                            else:
                                gate_results[ec["idx"]] = {"passed": False, "rejection_reason": f"gate_http_{gr.status_code}"}
                        except Exception as ge:
                            gate_results[ec["idx"]] = {"passed": False, "rejection_reason": f"gate_error: {ge}"}

                    gate_bar.progress(1.0)

                    valid_chunks = []
                    for ec in extracted:
                        gr = gate_results.get(ec["idx"], {})
                        c1 = (gr.get("c1_chunk_informative") or [0])[0] if isinstance(gr.get("c1_chunk_informative"), list) else 0
                        c2 = (gr.get("c2_chunk_reference_clarity") or [0])[0] if isinstance(gr.get("c2_chunk_reference_clarity"), list) else 0
                        c3 = (gr.get("c3_chunk_multi_suitability") or [0])[0] if isinstance(gr.get("c3_chunk_multi_suitability"), list) else 0
                        if c1 == 0 or c2 == 0:
                            continue
                        types_ok = [qt for qt in question_types if qt != "multi" or c3 == 1]
                        if types_ok:
                            valid_chunks.append({**ec, "types_ok": types_ok, "gate": gr})

                    rejected = len(extracted) - len(valid_chunks)
                    gate_status.text(
                        f"{len(valid_chunks)}/{len(extracted)} chunks passed gate, "
                        f"{rejected} rejected"
                    )
                    rejection_counts = Counter(
                        (gr.get("rejection_reason") or "unknown")
                        for gr in gate_results.values()
                        if not gr.get("passed")
                    )
                    if rejection_counts:
                        summary = ", ".join(
                            f"{reason}: {count}"
                            for reason, count in sorted(rejection_counts.items())
                        )
                        st.caption(f"Gate rejection summary: {summary}")

                _leave_warn.empty()

                if not valid_chunks:
                    st.error("All chunks were rejected by the gate. No questions will be generated.")
                    st.stop()

                # ── 3. Create queue + dataset ──
                queue_response = post("/queues/", json={
                    "name": queue_name,
                    "description": f"Queue for {dataset_run_name}",
                    "priority": 1,
                })
                if queue_response.status_code != 200:
                    st.error(f"Failed to create queue: {queue_response.status_code}")
                    st.stop()

                total_tasks_planned = sum(len(vc["types_ok"]) for vc in valid_chunks)
                valid_chunks_by_idx = {vc["idx"]: vc for vc in valid_chunks}

                if _using_existing:
                    ds_id = _chosen_id
                    st.info(f"Re-using existing dataset '{dataset_name}' (ID: {ds_id})")
                else:
                    dataset_response = post("/datasets/", json={
                        "name": dataset_run_name,
                        "description": dataset_description,
                        "source_document": document_name,
                        "questions": [],
                        "metadata": {
                            "ablation_testing": ablation_testing,
                            "queue_name": queue_name,
                            "pipeline_mode": pipeline_mode,
                            "gate_enabled": gate_enabled,
                            "refine_enabled": refine_enabled,
                            "experiment_source_document": document_name,
                            "question_types": question_types,
                            "num_chunks_processed": 0,
                            "total_chunks": total_chunks,
                            "chunks_passed_gate": len(valid_chunks),
                            "chunks_rejected": rejected,
                            "total_questions_generated": 0,
                            "questions_per_chunk": len(question_types),
                            "expected_questions": total_tasks_planned,
                            "generation_model_id": generation_model_id,
                            "validation_model_id": validation_model_id,
                            "created_at": datetime.now().isoformat(),
                            "status": "processing",
                        },
                    })
                    if dataset_response.status_code != 200:
                        st.error(f"Failed to create dataset: {dataset_response.status_code}")
                        st.stop()

                    dataset_result = dataset_response.json()
                    ds_id = dataset_result["dataset_id"]
                    st.success(f"Created dataset '{dataset_run_name}' (ID: {ds_id})")

                    # Save all chunks, including gate-rejected ones, for audit/export.
                    chunk_docs = []
                    for ec in extracted:
                        vc = valid_chunks_by_idx.get(ec["idx"])
                        gate_result = (
                            (vc or {}).get("gate")
                            or gate_results.get(ec["idx"])
                            or {"passed": False, "rejection_reason": "not_selected_for_generation"}
                        )
                        chunk_docs.append({
                            "chunk_index": ec["idx"],
                            "chunk_text": ec["text"],
                            "fragment_data": ec.get("fragment_data"),
                            "gate_result": gate_result,
                            "gate_passed": bool(vc),
                            "question_types_valid": vc["types_ok"] if vc else [],
                        })
                    post(f"/datasets/{ds_id}/chunks", json=chunk_docs)

                # ── 4. Create tasks from valid chunks ──
                tasks = []
                for vc in valid_chunks:
                    for qt in vc["types_ok"]:
                        td = {
                            "chunk_id": vc["idx"],
                            "chunk_text": vc["text"],
                            "question_type": qt,
                            "source_document": document_name,
                            "dataset_name": dataset_run_name,
                            "dataset_id": ds_id,
                            "dataset_description": dataset_description,
                            "priority": 1,
                            "chunk_pre_validated": True,
                            "pipeline_mode": pipeline_mode,
                        }
                        if generation_model_id:
                            td["generation_model_id"] = generation_model_id
                        if validation_model_id:
                            td["validation_model_id"] = validation_model_id
                        tasks.append(td)

                tasks_response = post(f"/queues/{queue_name}/tasks/", json=tasks)

                if tasks_response.status_code == 200:
                    tr = tasks_response.json()
                    st.success(
                        f"Added {tr['tasks_added']} tasks to queue '{queue_name}'. "
                        f"Go to Queue Manager to monitor progress."
                    )
                    st.page_link(
                        "views/queue_manager.py",
                        label="Open Queue Manager",
                        icon=":material/arrow_forward:",
                    )
                else:
                    st.error(f"Failed to add tasks to queue: {tasks_response.status_code}")

            except Exception as e:
                st.error(f"Error creating queue: {str(e)}")

        else:  # Direct Processing
            total_questions = total_chunks * len(question_types)
            st.info(f"Processing {total_chunks} chunks x {len(question_types)} question types = {total_questions} total questions...")

            progress_bar = st.progress(0)
            status_text = st.empty()

            if not API_GEN_QUE_URL:
                st.error("API_GEN_QUE_URL is not configured. Please check your environment variables.")
                st.stop()

            generate_url = API_GEN_QUE_URL
            question_counter = 0

            source_items = data.get("chunks_detailed", data.get("chunks", []))
            for idx, chunk in enumerate(source_items, 1):
                if isinstance(chunk, dict):
                    fd = chunk.get("fragment_data", {})
                    chunk_text = fd.get("combined_text", str(chunk))
                else:
                    chunk_text = str(chunk)

                for question_type in question_types:
                    question_counter += 1

                    progress = question_counter / total_questions
                    progress_bar.progress(progress)
                    status_text.text(f"Processing chunk {idx}/{total_chunks}, question type: {question_type} ({question_counter}/{total_questions})...")

                    payload = {
                        "prompt": chunk_text,
                        "question_type": question_type,
                        "source": "user_input",
                        "chat_id": 12345,
                        "source_text": chunk_text,
                        "pipeline_mode": pipeline_mode,
                    }
                    if generation_model_id:
                        payload["generation_model_id"] = generation_model_id
                    if validation_model_id:
                        payload["validation_model_id"] = validation_model_id
                    if _chunks_pre_validated:
                        payload["chunk_pre_validated"] = True

                    try:
                        res = requests.post(generate_url, json=payload)
                        if res.status_code == 200:
                            output = res.json().get("result", {}).get("output", {})

                            if output.get("chunk_rejected"):
                                status_text.text(f"Chunk {idx}/{total_chunks} rejected by gate ({question_type})")
                                continue

                            gq = output.get("generated_question") or {}
                            sensitivity = output.get("sensitivity_score") or {}
                            validation = output.get("validation_result") or {}

                            options = [
                                (int(k.replace("option_", "")), v)
                                for k, v in gq.items()
                                if k.startswith("option_") and v not in [None, "None"]
                            ]
                            options.sort()
                            options_text = "\n".join(f"{i}. {text}" for i, text in options) if options else "No options provided"

                            options_dict = {}
                            for k, v in gq.items():
                                if k.startswith("option_") and v not in [None, "None"]:
                                    options_dict[k] = v

                            results.append({
                                "Chunk #": idx,
                                "Source Chunk": chunk_text,
                                "Question Type": output.get("question_type", "unknown"),
                                "Task": gq.get("task", ""),
                                "Options": options_text,
                                "Options Dict": options_dict,
                                "Correct Answer": gq.get("outputs", "N/A"),
                                "Provocativeness": sensitivity.get("provocativeness_score", "N/A"),
                                "Validation Score": f"{validation.get('total', 'N/A')}/{validation.get('max_total', 'N/A')}",
                                "Validation Threshold": validation.get('threshold', 'N/A'),
                                "Validation Passed": validation.get('passed', False),
                                "Validation Details": validation.get('by_block', {}),
                                "Validation Justifications": validation.get('justifications', {}),
                                "Retry Count": output.get("retry_count", 0),
                            })
                        else:
                            results.append({
                                "Chunk #": idx,
                                "Source Chunk": chunk_text,
                                "Question Type": question_type,
                                "Task": f"Error: {res.status_code}",
                                "Options": "",
                                "Correct Answer": "",
                                "Provocativeness": "",
                                "Validation Score": "N/A",
                                "Validation Threshold": "N/A",
                                "Validation Passed": False,
                                "Validation Details": {},
                                "Validation Justifications": {},
                                "Retry Count": 0,
                            })
                    except requests.exceptions.RequestException as e:
                        results.append({
                            "Chunk #": idx,
                            "Source Chunk": chunk_text,
                            "Question Type": question_type,
                            "Task": f"Network error: {str(e)}",
                            "Options": "",
                            "Correct Answer": "",
                            "Provocativeness": "",
                            "Validation Score": "N/A",
                            "Validation Threshold": "N/A",
                            "Validation Passed": False,
                            "Validation Details": {},
                            "Validation Justifications": {},
                            "Retry Count": 0,
                        })

            progress_bar.progress(1.0)
            status_text.text("Generation completed!")
            _leave_warn.empty()
            st.success("Generation completed.")

        if results:
            st.session_state.results = results
            st.session_state.dataset_saved = False

    # ── Display results (from session_state — survives reruns) ──

    results = st.session_state.results
    if results:
        chunks_with_questions = {}
        for item in results:
            chunk_id = item['Chunk #']
            if chunk_id not in chunks_with_questions:
                chunks_with_questions[chunk_id] = []
            chunks_with_questions[chunk_id].append(item)

        for chunk_id, questions in chunks_with_questions.items():
            st.markdown("---")
            st.markdown(f"### Chunk #{chunk_id}")

            if questions and questions[0].get('Source Chunk'):
                with st.expander("View Source Text", expanded=False):
                    src = questions[0]['Source Chunk']
                    if isinstance(src, dict):
                        src = src.get("fragment_data", {}).get("combined_text", str(src))
                    st.markdown("**Source Text:**")
                    st.markdown(f"```\n{src}\n```")

            for i, item in enumerate(questions, 1):
                st.markdown(f"#### Question {i} ({item['Question Type']})")
                st.markdown(f"**Task:** {item['Task']}")
                st.markdown("**Options:**")
                st.markdown(item['Options'])
                st.markdown(f"**Correct Answer:** {item['Correct Answer']}")

                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**Provocativeness Score:** {item['Provocativeness']}")

                with col2:
                    validation_score = item['Validation Score']
                    validation_threshold = item.get('Validation Threshold', 'N/A')
                    validation_passed = item['Validation Passed']

                    if validation_passed:
                        st.markdown(f"**Validation:** {validation_score} (Threshold: {validation_threshold}) PASSED")
                    else:
                        st.markdown(f"**Validation:** {validation_score} (Threshold: {validation_threshold}) FAILED")

                st.markdown(format_retry_line(item.get("Retry Count", 0)))

                if item.get('Validation Details'):
                    with st.expander("View Validation Details", expanded=False):
                        validation_details = item['Validation Details']
                        justifications = item.get('Validation Justifications') or {}
                        st.markdown("**Validation Breakdown:**")
                        st.markdown(
                            format_validation_breakdown_md(validation_details, justifications)
                        )

                if i < len(questions):
                    st.markdown("---")

        # ── Save & download ──

        st.markdown("---")

        if st.session_state.dataset_saved:
            st.success("Dataset already saved.")
        else:
            if st.button("Save Dataset to Database", key="save_dataset_btn"):
                try:
                    questions_data = []
                    for item in results:
                        questions_data.append({
                            "chunk_id": item['Chunk #'],
                            "source_chunk": (
                                item['Source Chunk'].get("fragment_data", {}).get("combined_text", str(item['Source Chunk']))
                                if isinstance(item['Source Chunk'], dict)
                                else str(item['Source Chunk'])
                            ),
                            "question_type": item['Question Type'],
                            "task": item['Task'],
                            "options": item.get('Options Dict', item['Options']),
                            "correct_answer": str(item['Correct Answer']),
                            "provocativeness": str(item['Provocativeness']),
                            "validation_score": str(item['Validation Score']),
                            "validation_threshold": str(item.get('Validation Threshold', 'N/A')),
                            "validation_passed": str(item['Validation Passed']),
                            "validation_details": str(item['Validation Details']),
                            "validation_justifications": str(item.get('Validation Justifications', {})),
                            "retry_count": str(item.get('Retry Count', 0)),
                        })

                    dataset_payload = {
                        "name": dataset_run_name,
                        "description": dataset_description,
                        "source_document": document_name,
                        "questions": questions_data,
                        "metadata": {
                            "ablation_testing": ablation_testing,
                            "pipeline_mode": pipeline_mode,
                            "gate_enabled": gate_enabled,
                            "refine_enabled": refine_enabled,
                            "experiment_source_document": document_name,
                            "question_types": question_types,
                            "num_chunks_processed": data['num_chunks'],
                            "total_chunks": data['num_chunks'],
                            "total_questions_generated": len(results),
                            "questions_per_chunk": len(question_types),
                            "generation_model_id": generation_model_id,
                            "validation_model_id": validation_model_id,
                            "generated_at": datetime.now().isoformat()
                        }
                    }

                    save_response = post("/datasets/", json=dataset_payload)

                    if save_response.status_code == 200:
                        save_result = save_response.json()
                        st.success(f"Dataset saved! ID: {save_result['dataset_id']}")
                        st.session_state.dataset_saved = True
                    else:
                        st.error(f"Error saving dataset: {save_response.status_code}")
                        st.error(f"Response: {save_response.text}")

                except requests.exceptions.ConnectionError:
                    st.error("Could not connect to the Dataset API server.")
                except Exception as e:
                    st.error(f"Error saving dataset: {str(e)}")

        df = pd.DataFrame(results)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            "Download as CSV",
            data=csv,
            file_name="generated_tasks.csv",
            mime="text/csv",
            key="results_csv_download",
        )

elif not _using_existing:
    st.session_state.chunk_data = None
    st.session_state.chunk_file_id = None
    st.session_state.document_name = None
    st.session_state.results = None
    st.session_state.dataset_saved = False
    st.session_state.dataset_name_ts = None
