from gena.views.statistics import load_dataset
import streamlit as st
import requests
from gena.views import page_subtitle
import pandas as pd
from datetime import datetime
import time
from gena.config import API_DATASET_URL, AGENT_API_URL
from gena.http import get, post, put, delete
from collections import Counter


st.title("Queue Manager")
st.markdown("<a name='top'></a>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("**On this page**")
    st.markdown(
        """
- [Model Health](#model-health)
- [Queue Overview](#queue-overview)
- [Dataset Progress](#dataset-progress)
- [Manage Queues](#manage-queues)
        """
    )

page_subtitle("Monitor task queues, dataset progress, and model health.")
st.markdown(
    """
- [Model Health](#model-health)
- [Queue Overview](#queue-overview)
- [Dataset Progress](#dataset-progress)
- [Manage Queues](#manage-queues)
"""
)
st.markdown("---")


@st.cache_data(ttl=30)
def _fetch_models_health():
    try:
        resp = requests.get(
            f"{AGENT_API_URL}/models/health/", timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []


def _render_model_health_banner():
    health = _fetch_models_health()
    if not health:
        return {}
    health_map = {h["id"]: h for h in health}
    unavailable = [h for h in health if not h["available"]]
    if unavailable:
        names = ", ".join(f"**{h['name']}**" for h in unavailable)
        st.error(
            f"Models not responding: {names}. "
            "Tasks using these models will fail until they recover."
        )
    else:
        st.success(f"All {len(health)} models are healthy.")
    return health_map


st.markdown('<a name="model-health"></a>', unsafe_allow_html=True)
st.markdown("### Model Health")
_health_map = _render_model_health_banner()

# Функция для загрузки списка очередей
def load_queues():
    try:
        resp = get("/queues/")
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 401:
            st.error("⛔ Unauthorized (401). Please login again.")
            return []
        else:
            st.error(f"Error loading queues: {resp.status_code}")
            return []
    except Exception as e:
        st.error(f"⛔ Could not connect to the Task Queue API server: {e}")
        return []


# Функция для удаления очереди
def delete_queue(queue_name):
    try:
        resp = delete(f"/queues/{queue_name}")
        return resp.status_code == 200
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return False

# Функция для получения задач очереди
def get_queue_tasks(queue_name, status=None):
    try:
        params = {"status": status} if status else None
        resp = get(f"/queues/{queue_name}/tasks/", params=params)
        return resp.json() if resp.status_code == 200 else []
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return []

# Функция для загрузки датасетов
def load_datasets():
    try:
        resp = get("/datasets/")
        return resp.json() if resp.status_code == 200 else []
    except Exception as e:
        st.error(f"⛔ Could not connect to the Dataset API server: {e}")
        return []



def retry_failed_tasks(queue_name):
    try:
        resp = post(f"/queues/{queue_name}/retry-failed")
        if resp.status_code == 200:
            result = resp.json()
            st.success(f"✅ {result['message']}")
            return True
        else:
            st.error(f"Error retrying failed tasks: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return False


def __recount_queue_aggregates(queue_name: str) -> dict:
    tasks = get_queue_tasks(queue_name) or []
    c = Counter(t.get("status", "unknown") for t in tasks)
    return {
        "task_count": len(tasks),
        "pending_count": c.get("pending", 0),
        "processing_count": c.get("processing", 0),
        "completed_count": c.get("completed", 0),
        "failed_count": c.get("failed", 0),
        "cancelled_count": c.get("cancelled", 0),
    }

def __dataset_tasks_progress(dataset_id: str) -> dict:
    try:
        resp = get(f"/datasets/{dataset_id}/tasks")
        tasks = resp.json() if resp.status_code == 200 else []
    except Exception:
        tasks = []
    c = Counter(t.get("status", "unknown") for t in tasks)
    total = len(tasks)
    completed = c.get("completed", 0)
    failed = c.get("failed", 0)
    processing = c.get("processing", 0)
    pending = c.get("pending", 0)
    cancelled = c.get("cancelled", 0)

    # вычисляем статус
    if total == 0:
        status = "no_tasks"
    elif processing > 0 or pending > 0:
        status = "in_progress"
    elif completed > 0 and failed == 0 and processing == 0 and pending == 0:
        status = "completed"
    elif failed > 0 and processing == 0 and pending == 0:
        status = "completed_with_failures"
    else:
        status = "unknown"

    progress_pct = (completed / total * 100.0) if total > 0 else 0.0

    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "processing": processing,
        "pending": pending,
        "cancelled": cancelled,
        "status": status,
        "progress_pct": round(progress_pct, 1),
    }

# Основной интерфейс
st.markdown('<a name="queue-overview"></a>', unsafe_allow_html=True)
st.markdown("### 📊 Queue Overview")

# Загружаем список очередей и датасетов
queues = load_queues()
datasets = load_datasets()

if not queues and not datasets:
    st.info("No queues or datasets found. Create some queues first.")
else:
    # Отображаем статистику очередей
    st.markdown("#### Queue Statistics")
    
    # Создаем DataFrame для отображения статистики
    queue_stats = []
    for queue in queues:
        q_name = queue.get("name", "")
        task_count = queue.get("task_count")
        pending_count = queue.get("pending_count")
        processing_count = queue.get("processing_count")
        completed_count = queue.get("completed_count")
        failed_count = queue.get("failed_count")
        cancelled_count = queue.get("cancelled_count")

        need_recount = task_count in (None, 0)
        if not need_recount:
            zeros_sum = sum(int(x or 0) for x in [
                pending_count, processing_count, completed_count, failed_count, cancelled_count
            ])
            if zeros_sum == 0:
                need_recount = True

        if need_recount and q_name:
            agg = __recount_queue_aggregates(q_name)
            task_count = agg["task_count"]
            pending_count = agg["pending_count"]
            processing_count = agg["processing_count"]
            completed_count = agg["completed_count"]
            failed_count = agg["failed_count"]
            cancelled_count = agg["cancelled_count"]

        queue_stats.append({
            "Queue Name": queue["name"],
            "Description": queue.get("description", ""),
            "Priority": queue.get("priority", 1),
            "Total Tasks": task_count or 0,
            "Pending": pending_count or 0,
            "Processing": processing_count or 0,
            "Completed": completed_count or 0,
            "Failed": failed_count or 0,
            "Cancelled": cancelled_count or 0,
            "Created": queue.get("created_at", "")[:19] if queue.get("created_at") else ""
        })
    
    df_stats = pd.DataFrame(queue_stats)
    st.dataframe(df_stats, use_container_width=True)
    
    # Визуализация статистики
    if not df_stats.empty:
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### Tasks by Status")
            status_data = {
                "Pending": int(df_stats["Pending"].sum()),
                "Processing": int(df_stats["Processing"].sum()),
                "Completed": int(df_stats["Completed"].sum()),
                "Failed": int(df_stats["Failed"].sum()),
                "Cancelled": int(df_stats["Cancelled"].sum())
            }
            st.bar_chart(status_data)
        
        with col2:
            st.markdown("#### Tasks by Queue")
            queue_data = df_stats.set_index("Queue Name")["Total Tasks"]
            st.bar_chart(queue_data)

# Отображение прогресса датасетов
if datasets:
    st.markdown("---")
    st.markdown('<a name="dataset-progress"></a>', unsafe_allow_html=True)
    st.markdown("### 📊 Dataset Progress")
    
    # Создаем DataFrame для отображения прогресса датасетов
    dataset_stats = []
    for dataset in datasets:
        ds_full = load_dataset(dataset["_id"])
        metadata = (ds_full or {}).get("metadata", {})
        status = metadata.get("status", "unknown")
        questions = (ds_full or {}).get("questions", []) or []
        

        ds_prog = __dataset_tasks_progress(dataset.get("_id", ""))

        if ds_prog["total"] == 0 and questions:
            status = metadata.get("status", "completed")
            total_tasks = 0
            completed_tasks = 0
            failed_tasks = 0
            processing_tasks = 0
            pending_tasks = 0
            cancelled_tasks = 0
            total_q = metadata.get("total_questions_generated", len(questions))
            # Expected count must be based on chunks that actually entered
            # generation (gate-passed); ``total_chunks`` includes gate-rejected
            # ones and would keep a finished dataset below 100%.
            chunks_for_expected = (
                metadata.get("chunks_passed_gate") or metadata.get("total_chunks") or 0
            )
            qtypes = metadata.get("question_types", [])
            qpc = len(qtypes) if qtypes else 1
            expected = chunks_for_expected * qpc if chunks_for_expected else total_q
            if status == "completed":
                progress_percent = 100.0
            else:
                progress_percent = (total_q / expected * 100.0) if expected else 100.0
        else:
            status = ds_prog["status"]
            total_tasks = ds_prog["total"]
            completed_tasks = ds_prog["completed"]
            failed_tasks = ds_prog["failed"]
            processing_tasks = ds_prog["processing"]
            pending_tasks = ds_prog["pending"]
            cancelled_tasks = ds_prog["cancelled"]
            progress_percent = ds_prog["progress_pct"]

        if "total_chunks" in metadata and metadata.get("total_chunks") is not None:
            total_chunks = metadata.get("total_chunks") or 0
        else:
            total_chunks = dataset.get("chunks_count", 0) or 0
        chunks_valid = dataset.get("chunks_valid", 0)
        total_questions = metadata.get("total_questions_generated", len(questions))
        question_types = metadata.get("question_types", [])
        questions_per_chunk = len(question_types) if question_types else 1

        expected_questions = metadata.get("expected_questions")
        if expected_questions in (None, 0):
            expected_questions = total_tasks if total_tasks else (total_chunks * questions_per_chunk)

        dataset_stats.append({
            "Dataset Name": dataset["name"],
            "Status": status,
            "Chunks (valid/total)": f"{chunks_valid}/{total_chunks}",
            "Questions Generated": total_questions,
            "Expected Questions": expected_questions,
            "Progress %": f"{progress_percent:.1f}%",  
            "Created": dataset.get("created_at", "")[:19] if dataset.get("created_at") else "",
            "Last Updated": metadata.get("last_updated", "")[:19] if metadata.get("last_updated") else ""
        })
    
    df_datasets = pd.DataFrame(dataset_stats)
    st.dataframe(df_datasets, use_container_width=True)
    
    # Визуализация прогресса датасетов
    if not df_datasets.empty:
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("#### Dataset Progress")
            # Создаем прогресс-бары для каждого датасета
            for _, row in df_datasets.iterrows():
                progress = float(str(row["Progress %"]).replace("%", "")) if row.get("Progress %") else 0.0
                st.progress(progress / 100, text=f"{row['Dataset Name']}: {row['Progress %']}")
        
        with col2:
            st.markdown("#### Dataset Status")
            status_counts = df_datasets["Status"].value_counts()
            st.bar_chart(status_counts)

# Управление существующими очередями
if queues:
    st.markdown("---")
    st.markdown('<a name="manage-queues"></a>', unsafe_allow_html=True)
    st.markdown("### 🔧 Manage Queues")
    
    # Выбор очереди для управления
    queue_options = {f"{q['name']} ({q.get('task_count', 0)} tasks)": q['name'] for q in queues}
    selected_queue_name = st.selectbox("Select a queue to manage:", list(queue_options.keys()), key="queue_selector")
    
    if selected_queue_name:
        selected_queue = queue_options[selected_queue_name]
        
        # Информация о выбранной очереди
        queue_info = next((q for q in queues if q['name'] == selected_queue), None)
        
        if queue_info:
            all_tasks = get_queue_tasks(selected_queue)
            cc = Counter(t.get("status", "unknown") for t in all_tasks)

            # Determine which models are used in this queue's tasks
            queue_gen_models = set()
            queue_val_models = set()
            for t in all_tasks:
                gm = t.get("generation_model_id")
                vm = t.get("validation_model_id")
                if gm:
                    queue_gen_models.add(gm)
                if vm:
                    queue_val_models.add(vm)
            queue_all_models = queue_gen_models | queue_val_models

            if queue_all_models and _health_map:
                down_in_queue = [
                    mid for mid in queue_all_models
                    if mid in _health_map and not _health_map[mid].get("available", True)
                ]
                if down_in_queue:
                    names = ", ".join(
                        f"**{_health_map[m].get('name', m)}**" for m in down_in_queue
                    )
                    pending_or_processing = cc.get("pending", 0) + cc.get("processing", 0)
                    st.error(
                        f"This queue uses models that are currently down: {names}. "
                        f"{pending_or_processing} pending/processing tasks may fail."
                    )

            st.markdown(f"#### Queue: {selected_queue}")
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.metric("Total Tasks", len(all_tasks))
                st.metric("Pending", cc.get("pending", 0))
            
            with col2:
                st.metric("Processing", cc.get("processing", 0))
                st.metric("Completed", cc.get("completed", 0))
            
            with col3:
                st.metric("Failed", cc.get("failed", 0))
                st.metric("Cancelled", cc.get("cancelled", 0))
                
                # Кнопка retry failed задач
                if cc.get("failed", 0) > 0:
                    if st.button("🔄 Retry Failed Tasks", type="primary", key="retry_failed"):
                        if retry_failed_tasks(selected_queue):
                            st.rerun()
            
            # Фильтр по статусу задач
            status_filter = st.selectbox(
                "Filter by status:",
                ["All", "pending", "processing", "completed", "failed", "cancelled"],
                key="status_filter"
            )
            
            # Получаем задачи очереди
            tasks = all_tasks if status_filter == "All" else get_queue_tasks(selected_queue, status_filter)
            
            if tasks:
                st.markdown(f"#### Tasks ({len(tasks)} found)")
                
                # Отображаем задачи в таблице
                task_data = []
                for task in tasks:
                    error_info = ""
                    if task.get("status") == "failed" and task.get("error"):
                        error_info = task.get("error", "")[:100] + "..." if len(task.get("error", "")) > 100 else task.get("error", "")

                    gen_mid = task.get("generation_model_id", "")
                    val_mid = task.get("validation_model_id", "")
                    gen_label = gen_mid
                    val_label = val_mid
                    if _health_map:
                        if gen_mid and gen_mid in _health_map:
                            h = _health_map[gen_mid]
                            gen_label = h.get("name", gen_mid)
                            if not h.get("available", True):
                                gen_label = f"\u26a0 {gen_label}"
                        if val_mid and val_mid in _health_map:
                            h = _health_map[val_mid]
                            val_label = h.get("name", val_mid)
                            if not h.get("available", True):
                                val_label = f"\u26a0 {val_label}"

                    task_data.append({
                        "Task ID": task["_id"],
                        "Chunk ID": task.get("chunk_id", ""),
                        "Question Type": task.get("question_type", ""),
                        "Status": task.get("status", ""),
                        "Gen Model": gen_label,
                        "Val Model": val_label,
                        "Priority": task.get("priority", ""),
                        "Error": error_info,
                        "Created": task.get("created_at", "")[:19] if task.get("created_at") else "",
                        "Updated": task.get("updated_at", "")[:19] if task.get("updated_at") else ""
                    })
                
                df_tasks = pd.DataFrame(task_data)
                st.dataframe(df_tasks, use_container_width=True)
                
                # Показываем полные ошибки для failed задач
                failed_tasks = [task for task in tasks if task.get("status") == "failed" and task.get("error")]
                if failed_tasks:
                    st.markdown(f"#### 🔴 Failed Tasks - Full Error Details ({len(failed_tasks)} tasks)")
                    for task in failed_tasks:
                        with st.expander(f"❌ Task {task['_id']} - {task.get('question_type', 'N/A')} (Chunk {task.get('chunk_id', 'N/A')})", expanded=False):
                            st.error(f"**Error:** {task.get('error', 'No error details')}")
                            st.write(f"**Task ID:** {task['_id']}")
                            st.write(f"**Chunk ID:** {task.get('chunk_id', 'N/A')}")
                            st.write(f"**Question Type:** {task.get('question_type', 'N/A')}")
                            st.write(f"**Created:** {task.get('created_at', 'N/A')}")
                            st.write(f"**Updated:** {task.get('updated_at', 'N/A')}")
                
                # Детальная информация о задаче
                if st.checkbox("Show task details", key="show_details"):
                    task_id = st.selectbox("Select task ID:", [task["_id"] for task in tasks], key="task_detail_selector")
                    
                    if task_id:
                        selected_task = next((task for task in tasks if task["_id"] == task_id), None)
                        
                        if selected_task:
                            st.markdown("#### Task Details")
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                st.write("**Basic Info:**")
                                st.write(f"- Task ID: {selected_task['_id']}")
                                st.write(f"- Status: {selected_task.get('status', 'N/A')}")
                                st.write(f"- Priority: {selected_task.get('priority', 'N/A')}")
                                st.write(f"- Question Type: {selected_task.get('question_type', 'N/A')}")
                                st.write(f"- Chunk ID: {selected_task.get('chunk_id', 'N/A')}")
                            
                            with col2:
                                st.write("**Timestamps:**")
                                st.write(f"- Created: {selected_task.get('created_at', 'N/A')}")
                                st.write(f"- Updated: {selected_task.get('updated_at', 'N/A')}")
                            
                            # Показываем результат, если задача завершена
                            if selected_task.get("result"):
                                st.markdown("#### Task Result")
                                st.json(selected_task["result"])
                            
                            # Показываем ошибку, если задача провалилась
                            if selected_task.get("error"):
                                st.markdown("#### Task Error")
                                st.error(selected_task["error"])
                                
                                # Дополнительная информация об ошибке
                                st.markdown("**Error Details:**")
                                st.code(selected_task["error"], language="text")
                                
                                # Показываем контекст задачи для отладки
                                st.markdown("**Task Context for Debugging:**")
                                st.write(f"- **Chunk Text Preview:** {selected_task.get('chunk_text', 'N/A')[:200]}...")
                                st.write(f"- **Source Document:** {selected_task.get('source_document', 'N/A')}")
                                st.write(f"- **Dataset Name:** {selected_task.get('dataset_name', 'N/A')}")
            else:
                st.info("No tasks found for this queue/filter")
            
            # Кнопка удаления очереди
            st.markdown("---")
            if st.button("🗑️ Delete Queue", type="secondary"):
                if st.checkbox("I understand that this will delete the queue and all its tasks"):
                    if delete_queue(selected_queue):
                        st.success(f"✅ Queue '{selected_queue}' deleted successfully!")
                        st.rerun()

# Автообновление
if st.checkbox("🔄 Auto-refresh every 30 seconds", key="auto_refresh"):
    time.sleep(30)
    st.rerun()

st.markdown("---")
st.markdown("[⬆️ Back to top](#top)")