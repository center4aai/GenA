import streamlit as st
import os
import requests
import random
import pandas as pd
import tempfile
from datetime import datetime

from gena.config import API_CHANKS_URL, CHUNKS_DIR, API_GEN_QUE_URL, API_DATASET_URL, DOCS_DIR, AGENT_API_URL
from gena.http import get, post, put, delete

# ── Session state defaults ──
_DEFAULTS = {
    "chunk_data": None,
    "chunk_file_id": None,
    "document_name": None,
    "results": None,
    "dataset_saved": False,
    "dataset_name_ts": None,
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


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

st.title("Question Generator")

st.markdown(
    "Upload a document, choose question types, and generate a dataset. "
    "Results will appear in **Results & Editor**."
)
col_nav1, col_nav2, _spacer = st.columns([1, 1, 4])
with col_nav1:
    st.page_link("views/home.py", label="Home", icon=":material/home:")
with col_nav2:
    st.page_link("views/docs.py", label="Documentation", icon=":material/menu_book:")

st.markdown("---")

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
    st.markdown("### Upload your document")


# ── Upload & chunk (cached) ──

uploaded_file = st.file_uploader("Upload a document", type=['docx', 'txt', 'pdf'])

if uploaded_file is not None:
    st.success(f"File uploaded: {uploaded_file.name}")

    if st.session_state.dataset_name_ts is None:
        st.session_state.dataset_name_ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    dataset_name = st.text_input(
        "Dataset Name:",
        value=f"Dataset_{uploaded_file.name}_{st.session_state.dataset_name_ts}",
    )
    dataset_description = st.text_area("Dataset Description (optional):", value=f"Generated from {uploaded_file.name}")
    st.markdown("---")

    if not API_CHANKS_URL:
        st.error("⛔ API_CHANKS_URL is not configured. Please check your environment variables.")
        st.stop()

    data, document_name = chunk_document(uploaded_file)

    if data is not None:
        st.success(f"✅ Document successfully split into chunks. Number of chunks: {data['num_chunks']}")
    else:
        st.stop()

    question_types = st.multiselect(
        "Select question types to generate:",
        ['one', 'multi', 'open']
    )

    if not question_types:
        question_types = ['one', 'multi', 'open']

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
                help="Model used for question generation, provocativeness and difficulty assessment",
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
                help="Model used for quality validation of generated questions",
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
        help="Queue Mode: Add tasks to queue for background processing. Direct Processing: Process immediately (may be slower)."
    )

    # ── Generate ──

    if st.button("Generate Questions"):
        chunks = data.get("chunks", [])
        total_chunks = len(chunks)

        if total_chunks == 0:
            st.error("No chunks found in the document.")
            st.stop()

        total_questions = len(chunks) * len(question_types)
        results = []

        if processing_mode == "Queue Mode (Recommended)":
            st.info(f"📊 Adding {total_chunks} chunks × {len(question_types)} question types = {total_questions} total tasks to queue...")

            queue_name = f"queue_{dataset_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            try:
                queue_payload = {
                    "name": queue_name,
                    "description": f"Queue for {dataset_name}",
                    "priority": 1
                }

                queue_response = post("/queues/", json=queue_payload)

                if queue_response.status_code != 200:
                    st.error(f"Failed to create queue: {queue_response.status_code}")
                    st.stop()

                dataset_payload = {
                    "name": dataset_name,
                    "description": dataset_description,
                    "source_document": document_name,
                    "questions": [],
                    "metadata": {
                        "queue_name": queue_name,
                        "question_types": question_types,
                        "num_chunks_processed": 0,
                        "total_chunks": len(chunks),
                        "total_questions_generated": 0,
                        "questions_per_chunk": len(question_types),
                        "created_at": datetime.now().isoformat(),
                        "status": "processing"
                    }
                }

                dataset_response = post("/datasets/", json=dataset_payload)

                if dataset_response.status_code != 200:
                    st.error(f"Failed to create dataset: {dataset_response.status_code}")
                    st.stop()

                dataset_result = dataset_response.json()
                st.success(f"✅ Created dataset '{dataset_name}' with ID: {dataset_result['dataset_id']}")

                tasks = []
                skipped_chunks = 0
                for idx, chunk in enumerate(chunks, 1):
                    if isinstance(chunk, dict):
                        chunk_text = chunk.get("fragment_data", {}).get("combined_text", "")
                        if not chunk_text:
                            chunk_text = chunk.get("fragment_data", {}).get("content", "")
                        if not chunk_text:
                            chunk_text = chunk.get("fragment_data", {}).get("title", "")
                        if not chunk_text:
                            chunk_text = str(chunk)
                    else:
                        chunk_text = str(chunk)

                    if not chunk_text or len(chunk_text.strip()) < 10:
                        skipped_chunks += 1
                        st.warning(f"⚠️ Chunk {idx} пропущен: пустой или слишком короткий текст")
                        continue

                    for question_type in question_types:
                        task_data = {
                            "chunk_id": idx,
                            "chunk_text": chunk_text,
                            "question_type": question_type,
                            "source_document": document_name,
                            "dataset_name": dataset_name,
                            "dataset_id": dataset_result['dataset_id'],
                            "dataset_description": dataset_description,
                            "priority": 1,
                        }
                        if generation_model_id:
                            task_data["generation_model_id"] = generation_model_id
                        if validation_model_id:
                            task_data["validation_model_id"] = validation_model_id
                        tasks.append(task_data)

                if skipped_chunks > 0:
                    st.info(f"ℹ️ Пропущено {skipped_chunks} пустых чанков из {len(chunks)}")

                tasks_response = post(f"/queues/{queue_name}/tasks/", json=tasks)

                if tasks_response.status_code == 200:
                    result = tasks_response.json()
                    st.success(f"✅ Successfully added {result['tasks_added']} tasks to queue '{queue_name}'")
                    st.info(f"📋 Queue: {queue_name}")
                    st.info(f"📊 Dataset: {dataset_name} (ID: {dataset_result['dataset_id']})")
                    st.info(f"🔧 Tasks will be processed by the worker in the background")
                    st.info(f"📊 You can monitor progress in the Queue Manager page")

                    st.markdown(f"""
                    ### 📋 Monitor Progress
                    Go to the **Queue Manager** page to monitor the progress of your tasks.
                    Queue name: `{queue_name}`
                    Dataset name: `{dataset_name}`
                    """)
                else:
                    st.error(f"Failed to add tasks to queue: {tasks_response.status_code}")

            except Exception as e:
                st.error(f"Error creating queue: {str(e)}")

        else:  # Direct Processing
            st.info(f"📊 Processing {total_chunks} chunks × {len(question_types)} question types = {total_questions} total questions...")

            progress_bar = st.progress(0)
            status_text = st.empty()

            if not API_GEN_QUE_URL:
                st.error("⛔ API_GEN_QUE_URL is not configured. Please check your environment variables.")
                st.stop()

            generate_url = API_GEN_QUE_URL
            question_counter = 0

            for idx, chunk in enumerate(chunks, 1):
                if isinstance(chunk, dict):
                    chunk_text = chunk.get("fragment_data", {}).get("combined_text", str(chunk))
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
                    }
                    if generation_model_id:
                        payload["generation_model_id"] = generation_model_id
                    if validation_model_id:
                        payload["validation_model_id"] = validation_model_id

                    try:
                        res = requests.post(generate_url, json=payload)
                        if res.status_code == 200:
                            output = res.json().get("result", {}).get("output", {})
                            gq = output.get("generated_question", {})
                            sensitivity = output.get("sensitivity_score", {})
                            validation = output.get("validation_result", {})

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
                                "Source Chunk": chunk,
                                "Question Type": output.get("question_type", "unknown"),
                                "Task": gq.get("task", ""),
                                "Options": options_text,
                                "Options Dict": options_dict,
                                "Correct Answer": gq.get("outputs", "N/A"),
                                "Provocativeness": sensitivity.get("provocativeness_score", "N/A"),
                                "Validation Score": f"{validation.get('total', 'N/A')}/{validation.get('max_total', 'N/A')}",
                                "Validation Threshold": validation.get('threshold', 'N/A'),
                                "Validation Passed": validation.get('passed', False),
                                "Validation Details": validation.get('by_block', {})
                            })
                        else:
                            results.append({
                                "Chunk #": idx,
                                "Source Chunk": chunk,
                                "Question Type": question_type,
                                "Task": f"Error: {res.status_code}",
                                "Options": "",
                                "Correct Answer": "",
                                "Provocativeness": "",
                                "Validation Score": "N/A",
                                "Validation Threshold": "N/A",
                                "Validation Passed": False,
                                "Validation Details": {}
                            })
                    except requests.exceptions.RequestException as e:
                        results.append({
                            "Chunk #": idx,
                            "Source Chunk": chunk,
                            "Question Type": question_type,
                            "Task": f"Network error: {str(e)}",
                            "Options": "",
                            "Correct Answer": "",
                            "Provocativeness": "",
                            "Validation Score": "N/A",
                            "Validation Threshold": "N/A",
                            "Validation Passed": False,
                            "Validation Details": {}
                        })

            progress_bar.progress(1.0)
            status_text.text("✅ Generation completed!")
            st.success("✅ Generation completed.")

        # Сохраняем результаты в session_state
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
            st.markdown(f"### 🧩 Chunk #{chunk_id}")

            if questions and questions[0].get('Source Chunk'):
                with st.expander("📄 View Source Text", expanded=False):
                    st.markdown("**Source Text:**")
                    st.markdown(f"```\n{questions[0]['Source Chunk']}\n```")

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
                        st.markdown(f"**Validation:** ✅ {validation_score} (Threshold: {validation_threshold}) PASSED")
                    else:
                        st.markdown(f"**Validation:** ❌ {validation_score} (Threshold: {validation_threshold}) FAILED")

                if item['Validation Details']:
                    with st.expander("🔍 View Validation Details", expanded=False):
                        validation_details = item['Validation Details']
                        st.markdown("**Validation Breakdown:**")

                        for block_name, scores in validation_details.items():
                            if isinstance(scores, list):
                                total_score = sum(scores)
                                st.markdown(f"- **{block_name}**: {scores} (Total: {total_score})")
                            else:
                                st.markdown(f"- **{block_name}**: {scores}")

                if i < len(questions):
                    st.markdown("---")

        # ── Save & download ──

        st.markdown("---")

        if not st.session_state.dataset_saved:
            st.markdown("### 💾 Save Dataset to Database")

            try:
                questions_data = []
                for item in results:
                    questions_data.append({
                        "chunk_id": item['Chunk #'],
                        "source_chunk": item['Source Chunk'],
                        "question_type": item['Question Type'],
                        "task": item['Task'],
                        "options": item.get('Options Dict', item['Options']),
                        "correct_answer": str(item['Correct Answer']),
                        "provocativeness": str(item['Provocativeness']),
                        "validation_score": str(item['Validation Score']),
                        "validation_threshold": str(item.get('Validation Threshold', 'N/A')),
                        "validation_passed": str(item['Validation Passed']),
                        "validation_details": str(item['Validation Details'])
                    })

                dataset_payload = {
                    "name": dataset_name,
                    "description": dataset_description,
                    "source_document": document_name,
                    "questions": questions_data,
                    "metadata": {
                        "question_types": question_types,
                        "num_chunks_processed": data['num_chunks'],
                        "total_chunks": data['num_chunks'],
                        "total_questions_generated": len(results),
                        "questions_per_chunk": len(question_types),
                        "generated_at": datetime.now().isoformat()
                    }
                }

                save_response = post("/datasets/", json=dataset_payload)

                if save_response.status_code == 200:
                    save_result = save_response.json()
                    st.success(f"✅ Dataset saved successfully! Dataset ID: {save_result['dataset_id']}")
                    st.info(f"📊 Dataset '{dataset_name}' (version {save_result['version']}) has been saved to the database.")
                    st.session_state.dataset_saved = True
                else:
                    st.error(f"❌ Error saving dataset: {save_response.status_code}")
                    st.error(f"Response text: {save_response.text}")

            except requests.exceptions.ConnectionError:
                st.error("⛔ Could not connect to the Dataset API server. Make sure it is running.")
            except Exception as e:
                st.error(f"❌ Error saving dataset: {str(e)}")

        df = pd.DataFrame(results)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            "📥 Download as CSV",
            data=csv,
            file_name="generated_tasks.csv",
            mime="text/csv",
            key="results_csv_download",
        )
else:
    st.session_state.chunk_data = None
    st.session_state.chunk_file_id = None
    st.session_state.document_name = None
    st.session_state.results = None
    st.session_state.dataset_saved = False
    st.session_state.dataset_name_ts = None
