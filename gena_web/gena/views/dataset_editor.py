import streamlit as st
import os
from gena.http import get, post, put, delete
st.session_state["role"] = "expert"
is_expert = True
import pandas as pd
from datetime import datetime
from gena.config import API_DATASET_URL, LOGO
import base64
import plotly.express as px
from collections import Counter
import re

def get_base64_image(img_path):
    with open(img_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

def _parse_validation_score(score_str):
    """'17/17' -> (17, 17); иначе -> (None, None)"""
    try:
        if not score_str:
            return None, None
        parts = str(score_str).split("/")
        if len(parts) != 2:
            return None, None
        return int(parts[0]), int(parts[1])
    except Exception:
        return None, None

def _to_bool(val):
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("true", "passed", "yes", "1"):
            return True
        if v in ("false", "failed", "no", "0"):
            return False
    return None

def _is_passed(q):
    """
    Определяем passed: сначала по validation_passed,
    иначе по total >= threshold, если оба есть.
    """
    vp = _to_bool(q.get("validation_passed"))
    if vp is not None:
        return vp
    total, _max_total = _parse_validation_score(q.get("validation_score"))
    try:
        thr = int(q.get("validation_threshold")) if q.get("validation_threshold") not in (None, "N/A", "") else None
    except Exception:
        thr = None
    if total is not None and thr is not None:
        return total >= thr
    return None

def _norm_options(opts):
    """Единый вид options для сравнения и вывода"""
    if isinstance(opts, dict):
        parts = [f"{k}: {v}" for k, v in sorted(opts.items()) if v and v != "None"]
        return "\n".join(parts)
    return str(opts or "")

def _as_options_text(opts):
    """Привести options к человекочитаемому тексту (для сравнения/редактирования)."""
    if isinstance(opts, dict):
        parts = [f"{k}: {v}" for k, v in sorted(opts.items()) if v and v != "None"]
        return "\n".join(parts)
    return str(opts or "")

def _parse_options_text(text):
    """
    Обратная операция: 'k1: v1\\nk2: v2' -> dict.
    Пустые/кривые строки пропускаем осторожно.
    """
    result = {}
    if not text:
        return result
    for line in str(text).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k:
                result[k] = v
    return result

def _coerce_options_like(original_options, edited):
    """
    Привести edited options к типу, как в оригинале.
    - Если original dict: парсим текст -> dict (или оставляем dict, если уже dict)
    - Иначе: строка
    """
    if isinstance(original_options, dict):
        if isinstance(edited, dict):
            return edited
        return _parse_options_text(edited)
    return str(edited or "")

def _qid_key(dataset_id):
    return f"qid_map::{dataset_id}"

def _ensure_qid_map(dataset_id, n):
    """
    Локальная карта стабильных ID по индексам: {idx: 'datasetId:00001'}.
    Храним в session_state, чтобы можно было «присвоить» ID и старым версиям,
    где их не было, и сравнивать корректно.
    """
    key = _qid_key(dataset_id)
    if key not in st.session_state:
        st.session_state[key] = {}
    mp = st.session_state[key]
    for idx in range(n):
        if idx not in mp:
            mp[idx] = f"{dataset_id}:{idx:05d}"
    return mp

def _inject_qids_local(dataset_id, questions):
    """
    Вписать question_id в локальные вопросы, если его нет.
    Берём из локальной qid-карты по индексу.
    """
    qid_map = _ensure_qid_map(dataset_id, len(questions))
    for idx, q in enumerate(questions):
        if not q.get("question_id"):
            q["question_id"] = qid_map.get(idx) or f"{dataset_id}:{idx:05d}"

def _carry_qids_for_save(dataset_id, updated_questions, original_questions):
    """
    Перед сохранением: гарантируем, что у каждого вопроса есть устойчивый question_id.
    Если в original он был — копируем; если нет — берём из локальной qid-карты.
    """
    qid_map = _ensure_qid_map(dataset_id, max(len(updated_questions), len(original_questions)))
    for idx, uq in enumerate(updated_questions):
        qid = None
        if idx < len(original_questions):
            qid = original_questions[idx].get("question_id")
        if not qid:
            qid = qid_map.get(idx) or f"{dataset_id}:{idx:05d}"
        uq["question_id"] = qid

def _norm_question_for_diff(q):
    return {
        "question_type": q.get("question_type", ""),
        "task": q.get("task", ""),
        "options": _norm_options(q.get("options")),
        "correct_answer": str(q.get("correct_answer", "")),
        "provocativeness": str(q.get("provocativeness", "")),
        "difficulty": str(q.get("difficulty", "")),
        "validation_score": str(q.get("validation_score", "")),
        "validation_passed": str(q.get("validation_passed", "")),
    }

def _compare_questions(q1, q2):
    """
    Сравнить 2 вопроса (нормализованные dict'ы).
    Вернуть словарь {field: (v1, v2)} только для отличающихся полей.
    """
    diffs = {}
    keys = ["question_type", "task", "options", "correct_answer", "provocativeness", "difficulty"]
    for k in keys:
        if (q1.get(k) or "") != (q2.get(k) or ""):
            diffs[k] = (q1.get(k, ""), q2.get(k, ""))
    return diffs

def _diff_versions(ds1, ds2):
    """
    Сравнение по стабильному question_id.
    Если в одной из версий нет question_id — фоллбэк к сравнению по индексу.
    """
    q1 = ds1.get("questions", []) or []
    q2 = ds2.get("questions", []) or []

    has_ids_1 = any(q.get("question_id") for q in q1)
    has_ids_2 = any(q.get("question_id") for q in q2)

    diffs = []

    if has_ids_1 and has_ids_2:
        map1 = {str(q.get("question_id")): i for i, q in enumerate(q1) if q.get("question_id")}
        map2 = {str(q.get("question_id")): i for i, q in enumerate(q2) if q.get("question_id")}
        all_ids = sorted(set(map1.keys()) | set(map2.keys()))
        for qid in all_ids:
            i = map1.get(qid)
            j = map2.get(qid)
            q1n = _norm_question_for_diff(q1[i]) if i is not None else {}
            q2n = _norm_question_for_diff(q2[j]) if j is not None else {}
            d = _compare_questions(q1n, q2n)
            if d:
                diffs.append({
                    "qid": qid,
                    "diffs": d,
                    "meta": {"v1_idx": i, "v2_idx": j}
                })
    else:
        n = max(len(q1), len(q2))
        for idx in range(n):
            a = q1[idx] if idx < len(q1) else {}
            b = q2[idx] if idx < len(q2) else {}
            q1n = _norm_question_for_diff(a)
            q2n = _norm_question_for_diff(b)
            d = _compare_questions(q1n, q2n)
            if d:
                diffs.append({
                    "qid": f"idx:{idx}",
                    "diffs": d,
                    "meta": {
                        "v1_idx": idx if idx < len(q1) else None,
                        "v2_idx": idx if idx < len(q2) else None
                    }
                })
    return diffs

def _ensure_edit_cache():
    if "edits" not in st.session_state:
        st.session_state.edits = {}

def _reset_question_widgets():
    """
    Чистим ВСЕ поля виджетов вопросов,
    чтобы при смене датасета/версии/страницы не подтягивались старые значения.
    """
    pat = re.compile(r'(^|.*::)(type|prov|task|options|answer|diff)_\d+$')
    for k in list(st.session_state.keys()):
        if pat.match(k):
            del st.session_state[k]

def _k(ctx: str, name: str, i_abs: int) -> str:
    """Единый способ формировать ключи виджетов, завязанных на контекст (dataset_id:version)."""
    return f"{ctx}::{name}_{i_abs}"

def _save_current_page_draft(ctx, page_questions, start):
    """
    Сохраняем в черновик только вопросы, где поля реально отличаются от базы.
    Если изменений нет — соответствующую запись из черновика удаляем.
    """
    _ensure_edit_cache()
    for i, q in enumerate(page_questions, start=start + 1):  # i = 1-based индекс в UI
        idx = i - 1  # 0-based индекс в массиве questions

        # Базовые значения из оригинала
        base_qtype = q.get("question_type", "")
        base_prov  = str(q.get("provocativeness", ""))
        base_task  = q.get("task", "") or ""
        base_opts  = _as_options_text(q.get("options"))
        base_ans   = str(q.get("correct_answer", ""))
        base_diff  = str(q.get("difficulty", "") or "")

        cur_qtype = st.session_state.get(_k(ctx, "type", i), base_qtype)
        cur_prov  = str(st.session_state.get(_k(ctx, "prov", i), base_prov))
        cur_task  = st.session_state.get(_k(ctx, "task", i), base_task)
        cur_opts_text = st.session_state.get(_k(ctx, "options", i), base_opts)
        cur_ans   = str(st.session_state.get(_k(ctx, "answer", i), base_ans))
        cur_diff  = str(st.session_state.get(_k(ctx, "diff", i), base_diff))

        # Нормализованное сравнение
        changed = (
            str(cur_qtype) != str(base_qtype) or
            str(cur_prov)  != str(base_prov)  or
            str(cur_task)  != str(base_task)  or
            str(cur_ans)   != str(base_ans)   or
            str(cur_opts_text) != str(base_opts) or
            str(cur_diff)  != str(base_diff)
        )

        if changed:
            edited = {
                "question_id": q.get("question_id"),
                "chunk_id": q.get("chunk_id"),
                "question_type": cur_qtype,
                "task": cur_task,
                "options": _coerce_options_like(q.get("options"), cur_opts_text),
                "correct_answer": cur_ans,
                "provocativeness": cur_prov,
                "difficulty": (None if cur_diff in ("", "—") else cur_diff),
            }
            for k in ("validation_passed", "validation_score", "validation_threshold",
                      "validation_details", "source_chunk"):
                if q.get(k) is not None:
                    edited[k] = q[k]
            st.session_state.edits[idx] = edited
        else:
            if idx in st.session_state.edits:
                del st.session_state.edits[idx]

def _apply_cache_defaults(ctx, i_abs, q):
    """
    Перед рендером виджетов запишем в session_state значения из кэша,
    чтобы при возврате на страницу поля заполнялись последними черновиками.
    """
    _ensure_edit_cache()
    idx = i_abs - 1
    cached = st.session_state.edits.get(idx)
    if not cached:
        return
    st.session_state.setdefault(_k(ctx, "type", i_abs), cached.get("question_type", q.get("question_type", "open")))
    st.session_state.setdefault(_k(ctx, "prov", i_abs), str(cached.get("provocativeness", q.get("provocativeness", "2"))))
    st.session_state.setdefault(_k(ctx, "task", i_abs), cached.get("task", q.get("task", "")))
    if isinstance(cached.get("options"), dict):
        opts_text = ""
        for k, v in cached["options"].items():
            if v and v != "None":
                opts_text += f"{k}: {v}\n"
    else:
        opts_text = str(cached.get("options", "") or "")
    st.session_state.setdefault(_k(ctx, "options", i_abs), opts_text)
    st.session_state.setdefault(_k(ctx, "answer", i_abs), str(cached.get("correct_answer", q.get("correct_answer", ""))))
    st.session_state.setdefault(_k(ctx, "diff", i_abs), str(cached.get("difficulty", q.get("difficulty", "")) or ""))

# ---------- UI ----------
st.title("GENA: Dataset Editor")

# Логотип
if os.path.exists(LOGO):
    img_base64 = get_base64_image(LOGO)
    st.markdown(
        f"""
        <div style="display: flex; justify-content: center;">
            <img src="data:image/png;base64,{img_base64}" width="520"/>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown("---")
st.markdown("""
## 📝 Dataset Editor

This page allows you to view, edit, and manage datasets that have been generated. You can:
- View all available datasets
- Select different versions of datasets
- View source text for each question (click to expand)
- Edit questions and answers
- Save changes as new versions
- Compare different versions
""")

def load_datasets():
    try:
        resp = get("/datasets/")
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 401:
            st.error("⛔ Unauthorized (401). Please login again.")
            return []
        else:
            st.error(f"Error loading datasets: {resp.status_code}")
            return []
    except Exception as e:
        st.error(f"⛔ Could not connect to the Dataset API server: {e}")
        return []

def load_dataset(dataset_id, version=None):
    try:
        params = {"version": version} if version is not None else None
        resp = get(f"/datasets/{dataset_id}", params=params)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 401:
            st.error("⛔ Unauthorized (401). Please login again.")
            return None
        else:
            st.error(f"Error loading dataset: {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return None

def load_dataset_versions(dataset_id):
    try:
        resp = get(f"/datasets/{dataset_id}/versions")
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 401:
            st.error("⛔ Unauthorized (401). Please login again.")
            return []
        else:
            st.error(f"Error loading versions: {resp.status_code}")
            return []
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return []

def update_dataset(dataset_id, questions_data, metadata=None):
    try:
        payload = {
            "questions": questions_data,
            "metadata": metadata or {}
        }
        resp = put(f"/datasets/{dataset_id}", json=payload)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 401:
            st.error("⛔ Unauthorized (401). Please login again.")
            return None
        else:
            st.error(f"Error updating dataset: {resp.status_code}")
            return None
    except Exception as e:
        st.error(f"Error: {str(e)}")
        return None

st.markdown("### 📊 Available Datasets")

datasets = load_datasets()

if not datasets:
    st.info("No datasets found. Generate some datasets first using the Question Generator.")
else:
    def _short(x):
        return str(x)[:8] if x else "?"

    datasets_sorted = sorted(datasets, key=lambda d: (d.get("name", ""), d.get("_id", "")))
    by_id = {d["_id"]: d for d in datasets_sorted}
    ids = list(by_id.keys())

    default_id = st.session_state.get("selected_dataset_id", ids[0] if ids else None)
    default_index = ids.index(default_id) if default_id in ids else 0

    selected_dataset_id = st.selectbox(
        "Select a dataset:",
        options=ids,
        index=default_index,
        format_func=lambda ds_id: f"{by_id[ds_id]['name']} (v{by_id[ds_id].get('current_version','?')}) — {_short(ds_id)}",
        key="dataset_selector_id",
    )

    st.session_state["selected_dataset_id"] = selected_dataset_id
    selected_ds = by_id[selected_dataset_id]
    if selected_ds:
        selected_dataset_id = selected_ds["_id"]

        versions = load_dataset_versions(selected_dataset_id)

        if versions:
            version_options = {f"Version {v['version']} ({v['created_at'][:10]})": v['version'] for v in versions}
            selected_version_name = st.selectbox(
                "Select version:",
                list(version_options.keys()),
                key=f"version_selector::{selected_dataset_id}"
            )
            selected_version = version_options[selected_version_name]

            _ctx = f"{selected_dataset_id}:{selected_version}"
            _prev = st.session_state.get("page_ctx")
            if _prev != _ctx:
                st.session_state.page = 1
                st.session_state.page_ctx = _ctx
                st.session_state.pop("edits", None)
                _reset_question_widgets()
                st.rerun()

            # Загружаем выбранную версию
            dataset = load_dataset(selected_dataset_id, selected_version)

            if dataset:
                _inject_qids_local(selected_dataset_id, dataset.get("questions", []) or [])

                st.markdown("---")
                st.markdown(f"### 📋 Dataset: {dataset['name']}")
                st.markdown(f"**Description:** {dataset.get('description', 'No description')}")
                st.markdown(f"**Source Document:** {dataset['source_document']}")
                st.markdown(f"**Current Version:** {dataset['current_version']}")
                st.markdown(f"**Viewing Version:** {dataset['requested_version']}")
                st.markdown(f"**Created:** {dataset['created_at'][:19]}")
                st.markdown(f"**Updated:** {dataset['updated_at'][:19]}")

                #  Question number + Avg validation rate
                questions = dataset.get("questions", [])
                total_questions = len(questions)

                passed_flags = []
                for q in questions:
                    pv = _is_passed(q)
                    if pv is not None:
                        passed_flags.append(pv)

                if passed_flags:
                    avg_validation_rate = (sum(1 for x in passed_flags if x) / len(passed_flags)) * 100
                    rate_str = f"{avg_validation_rate:.1f}%"
                else:
                    rate_str = "—"

                st.markdown("### 📈 Dataset statistics")
                k1, k2 = st.columns(2)
                with k1:
                    st.metric("Question number", total_questions)
                with k2:
                    st.metric("Avg validation rate", rate_str)

                st.markdown("---")
                st.markdown("### 📊 Distributions")
                c1, c2, c3 = st.columns(3)

                #  Типы вопросов
                with c1:
                    if questions:
                        s = pd.Series([q.get("question_type", "—") for q in questions]).value_counts()
                        if not s.empty:
                            df_types = s.rename_axis("type").reset_index(name="count")
                            fig = px.bar(df_types, x="type", y="count", title="By Question Type", text="count")
                            fig.update_layout(xaxis_title=None, yaxis_title=None)
                            fig.update_traces(marker_line_width=1, marker_line_color="white")
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info("No data for question types")
                    else:
                        st.info("No questions")

                # Провокативность (чувствительность)
                with c2:
                    if questions:
                        prov_vals = []
                        for q in questions:
                            v = str(q.get("provocativeness", "")).strip()
                            if v in ("1", "2", "3"):
                                prov_vals.append(int(v))
                        if prov_vals:
                            counts = pd.Series(prov_vals).value_counts().reindex([1, 2, 3], fill_value=0)
                            df_prov = counts.rename_axis("provocativeness").reset_index(name="count")
                            fig = px.bar(df_prov, x="provocativeness", y="count", title="By Provocativeness", text="count")
                            fig.update_layout(xaxis=dict(type="category", categoryorder="array", categoryarray=[1,2,3]),
                                              yaxis_title=None, xaxis_title=None)
                            fig.update_traces(marker_line_width=1, marker_line_color="white")
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            st.info("No data for provocativeness")
                    else:
                        st.info("No questions")

                # Validation total score
                with c3:
                    scores = []
                    for q in questions:
                        total, _max_ = _parse_validation_score(q.get("validation_score"))
                        if total is not None:
                            scores.append(int(total))
                    if scores:
                        mn, mx = min(scores), max(scores)
                        all_vals = list(range(mn, mx + 1))
                        cnt = Counter(scores)
                        df_scores = pd.DataFrame({"score": all_vals, "count": [cnt.get(v, 0) for v in all_vals]})
                        fig = px.bar(df_scores, x="score", y="count", title="By Validation Total Score", text="count")
                        fig.update_layout(xaxis_title="total", yaxis_title="count", xaxis=dict(tickmode="linear", dtick=1))
                        fig.update_traces(marker_line_width=1, marker_line_color="white")
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("No validation scores to plot")

                # ---------- Questions ----------
                st.markdown("---")
                st.markdown("<a name='top'></a>", unsafe_allow_html=True)  # Якорь для возврата наверх
                st.markdown("### ❓ Questions")

                page_size = 10
                total_questions = len(questions)
                num_pages = (total_questions - 1) // page_size + 1 if total_questions > 0 else 1

                if "page" not in st.session_state:
                    st.session_state.page = 1
                st.session_state.page = max(1, min(st.session_state.page, num_pages))

                st.session_state.page = st.number_input(
                    "Page",
                    min_value=1, max_value=num_pages, value=st.session_state.page, step=1,
                    key=f"page_selector::{_ctx}"
                )

                start = (st.session_state.page - 1) * page_size
                end = start + page_size
                page_questions = questions[start:end]

                # --- вывод вопросов ---
                if is_expert:
                    with st.form(f"edit_questions::{_ctx}::page_{st.session_state.page}"):
                        edited_questions = []
                        for i, question in enumerate(page_questions, start=start + 1):
                            _apply_cache_defaults(_ctx, i, question)

                            st.markdown(f"#### Question {i} (Chunk {question.get('chunk_id','—')})")

                            if question.get('source_chunk'):
                                with st.expander("📄 View Source Text", expanded=False):
                                    st.markdown("**Source Text:**")
                                    st.markdown(f"```\n{question['source_chunk']}\n```")

                            col1, col2 = st.columns(2)
                            with col1:
                                qtype_list = ['one', 'multi', 'open']
                                qtype_val = st.session_state.get(_k(_ctx, "type", i), question.get('question_type', 'open'))
                                qtype_idx = qtype_list.index(qtype_val) if qtype_val in qtype_list else 2
                                question_type = st.selectbox(
                                    f"Type {i}:",
                                    qtype_list,
                                    index=qtype_idx,
                                    key=_k(_ctx, "type", i),
                                )
                            with col2:
                                prov_list = ['1', '2', '3']
                                prov_val = str(st.session_state.get(_k(_ctx, "prov", i), str(question.get('provocativeness', '2'))))
                                prov_idx = prov_list.index(prov_val) if prov_val in prov_list else 1
                                provocativeness = st.selectbox(
                                    f"Provocativeness {i}:",
                                    prov_list,
                                    index=prov_idx,
                                    key=_k(_ctx, "prov", i),
                                )

                                diff_list = ['—', '1', '2', '3']
                                raw_diff = str(st.session_state.get(_k(_ctx, "diff", i), str(question.get('difficulty', '') or ''))).strip()
                                diff_idx = diff_list.index(raw_diff) if raw_diff in diff_list else 0
                                difficulty_choice = st.selectbox(
                                    f"Difficulty level {i}:",
                                    diff_list,
                                    index=diff_idx,
                                    key=_k(_ctx, "diff", i),
                                )
                                difficulty_to_save = None if difficulty_choice == '—' else difficulty_choice

                            task = st.text_area(
                                f"Task {i}:",
                                value=st.session_state.get(_k(_ctx, "task", i), question.get('task', '')),
                                height=100,
                                key=_k(_ctx, "task", i),
                            )

                            # options -> в текст (key: value)
                            options_value = st.session_state.get(_k(_ctx, "options", i))
                            if options_value is None:
                                ov = question.get('options', '')
                                if isinstance(ov, dict):
                                    options_text = ""
                                    for k, v in ov.items():
                                        if v and v != "None":
                                            options_text += f"{k}: {v}\n"
                                else:
                                    options_text = str(ov or "")
                            else:
                                options_text = options_value

                            options = st.text_area(
                                f"Options {i}:",
                                value=options_text,
                                height=80,
                                key=_k(_ctx, "options", i),
                            )

                            correct_answer = st.text_area(
                                f"Correct Answer {i}:",
                                value=st.session_state.get(_k(_ctx, "answer", i), str(question.get('correct_answer', ''))),
                                height=60,
                                key=_k(_ctx, "answer", i),
                            )

                            if question.get('validation_passed') or question.get('validation_score'):
                                v1, v2 = st.columns(2)
                                with v1:
                                    vp = question.get('validation_passed', 'N/A')
                                    vp_str = str(vp)
                                    if vp_str.lower() in ('true', 'passed'):
                                        st.success("✅ Validation: PASSED")
                                    elif vp_str.lower() in ('false', 'failed'):
                                        st.error("❌ Validation: FAILED")
                                    else:
                                        st.info(f"ℹ️ Validation: {vp}")
                                with v2:
                                    vs = question.get('validation_score', 'N/A')
                                    vt = question.get('validation_threshold', 'N/A')
                                    st.info(f"📊 Score: {vs} (Threshold: {vt})")

                                if question.get('validation_details'):
                                    with st.expander("🔍 Validation Details", expanded=False):
                                        st.json(question['validation_details'])

                            edited_question = {
                                "question_id": question.get('question_id'),
                                "chunk_id": question.get('chunk_id'),
                                "question_type": question_type,
                                "task": task,
                                "options": options,
                                "correct_answer": correct_answer,
                                "provocativeness": provocativeness,
                                "difficulty": difficulty_to_save,
                            }
                            for k in ("validation_passed", "validation_score", "validation_threshold", "validation_details", "source_chunk"):
                                if question.get(k) is not None:
                                    edited_question[k] = question[k]

                            edited_questions.append(edited_question)
                            st.markdown("---")

                        if st.form_submit_button("💾 Save Changes as New Version"):
                            if edited_questions:
                                # перед сохранением добавим текущую страницу в черновик
                                _save_current_page_draft(_ctx, page_questions, start)

                                metadata = dataset.get('metadata', {}) or {}
                                metadata['edited_at'] = datetime.now().isoformat()
                                metadata['edited_by'] = st.session_state.get('username', 'expert')

                                updated_questions = []
                                _ensure_edit_cache()

                                # если есть черновик для индекса — ставим его, иначе оригинал
                                for idx, q in enumerate(questions):
                                    if idx in st.session_state.edits:
                                        updated_questions.append(st.session_state.edits[idx])
                                    else:
                                        passthrough = {
                                            "question_id": q.get('question_id'),
                                            "chunk_id": q.get('chunk_id'),
                                            "question_type": q.get('question_type', ''),
                                            "task": q.get('task', ''),
                                            "options": q.get('options', ''),
                                            "correct_answer": q.get('correct_answer', ''),
                                            "provocativeness": q.get('provocativeness', ''),
                                            "difficulty": q.get('difficulty', None),
                                        }
                                        for k in ("validation_passed", "validation_score", "validation_threshold", "validation_details", "source_chunk"):
                                            if q.get(k) is not None:
                                                passthrough[k] = q[k]
                                        updated_questions.append(passthrough)

                                _carry_qids_for_save(selected_dataset_id, updated_questions, questions)

                                result = update_dataset(selected_dataset_id, updated_questions, metadata)
                                if result:
                                    st.success(f"✅ Dataset updated successfully! New version: {result['new_version']}")
                                    # очищаем черновики после успешной публикации
                                    st.session_state.pop("edits", None)
                                    st.rerun()
                            else:
                                st.warning("No questions to save")
                else:
                    st.info("🔒 Read-only: You are logged in as a regular user. Editing is available for experts only.")
                    for i, question in enumerate(page_questions, start=start + 1):
                        st.markdown(f"#### Question {i} (Chunk {question.get('chunk_id','—')})")
                        if question.get('source_chunk'):
                            with st.expander("📄 View Source Text", expanded=False):
                                st.markdown("**Source Text:**")
                                st.markdown(f"```\n{question['source_chunk']}\n```")
                        st.write(f"**Type:** {question.get('question_type','—')}")
                        sens_val = question.get("sensitivity_level") or question.get("provocativeness")
                        if sens_val not in (None, "", "None"):
                            st.write(f"**Sensitivity level:** {sens_val}")
                        diff_val = question.get("difficulty_level") or question.get("difficulty")
                        if diff_val not in (None, "", "None"):
                            st.write(f"**Difficulty level:** {diff_val}")
                        st.write(f"**Task:** {question.get('task','—')}")
                        st.write(f"**Options:** {_norm_options(question.get('options')) or '—'}")
                        st.write(f"**Correct Answer:** {question.get('correct_answer','—')}")
                        if question.get('validation_passed') or question.get('validation_score'):
                            v1, v2 = st.columns(2)
                            with v1:
                                vp = question.get('validation_passed', 'N/A')
                                st.write(f"Validation: {vp}")
                            with v2:
                                vs = question.get('validation_score', 'N/A')
                                vt = question.get('validation_threshold', 'N/A')
                                st.write(f"Score: {vs} (Thr: {vt})")
                        st.markdown("---")

                # --- Кнопки навигации снизу ---
                col1, col2, col3 = st.columns([1, 1, 1])
                with col1:
                    if st.session_state.page > 1:
                        if st.button("⬅️ Prev"):
                            if is_expert:
                                _save_current_page_draft(_ctx, page_questions, start)
                            _reset_question_widgets()
                            st.session_state.page -= 1
                            st.rerun()
                with col2:
                    _ensure_edit_cache()
                    edits_count = len(st.session_state.edits)
                    st.markdown(f"**Page {st.session_state.page}/{num_pages}**  &nbsp;&nbsp; _draft edits: {edits_count}_", unsafe_allow_html=True)
                with col3:
                    if st.session_state.page < num_pages:
                        if st.button("Next ➡️"):
                            if is_expert:
                                _save_current_page_draft(_ctx, page_questions, start)
                            _reset_question_widgets()
                            st.session_state.page += 1
                            st.rerun()

                # Ссылка наверх
                st.markdown("[⬆️ Back to top](#top)")

                st.markdown("---")
                st.markdown("### 📥 Export")

                df = pd.DataFrame(questions)
                df = df.rename(columns={
                    "provocativeness": "sensitivity_level",
                    "difficulty": "difficulty_level",
                })
                core_order = [
                    "question_id", "chunk_id", "question_type", "task", "options", "correct_answer",
                    "sensitivity_level", "difficulty_level",
                    "validation_passed", "validation_score", "validation_threshold",
                ]
                rest_cols = [c for c in df.columns if c not in core_order]
                final_order = [c for c in core_order if c in df.columns] + rest_cols

                df_export = df[final_order]
                csv_str = df_export.to_csv(index=False)
                st.download_button(
                    "📥 Download as CSV (includes source text)",
                    data=csv_str,
                    file_name=f"{dataset['name']}_v{dataset['requested_version']}.csv",
                    mime="text/csv",
                    key="dataset_csv_download"
                )

                if len(versions) > 1:
                    st.markdown("---")
                    st.markdown("### 🔍 Version Comparison (only differences)")

                    col1, col2 = st.columns(2)
                    with col1:
                        version1 = st.selectbox("Version 1:", [v['version'] for v in versions], key="v1")
                    with col2:
                        version2 = st.selectbox("Version 2:", [v['version'] for v in versions], key="v2")

                    if version1 != version2:
                        if st.button("Compare Versions", key="compare_btn"):
                            dataset1 = load_dataset(selected_dataset_id, version1)
                            dataset2 = load_dataset(selected_dataset_id, version2)

                            if dataset1 and dataset2:
                                _inject_qids_local(selected_dataset_id, dataset1.get("questions", []) or [])
                                _inject_qids_local(selected_dataset_id, dataset2.get("questions", []) or [])

                                st.markdown(f"#### Comparing Version {version1} vs Version {version2}")
                                st.markdown(f"**Version {version1}:** {len(dataset1.get('questions', []))} questions")
                                st.markdown(f"**Version {version2}:** {len(dataset2.get('questions', []))} questions")

                                diffs = _diff_versions(dataset1, dataset2)
                                if not diffs:
                                    st.success("✅ No differences between selected versions")
                                else:
                                    st.info(f"Found {len(diffs)} differing questions")
                                    for d in diffs:
                                        qid = d.get("qid")
                                        meta = d["meta"]
                                        st.markdown(f"##### • Question id: {qid}  (v{version1} idx={meta.get('v1_idx')}, v{version2} idx={meta.get('v2_idx')})")
                                        for field, (v1, v2) in d["diffs"].items():
                                            with st.expander(f"Changed field: {field}", expanded=False):
                                                c1, c2 = st.columns(2)
                                                with c1:
                                                    st.markdown(f"**v{version1}**")
                                                    st.code(str(v1) if v1 is not None else "—")
                                                with c2:
                                                    st.markdown(f"**v{version2}**")
                                                    st.code(str(v2) if v2 is not None else "—")
                                st.markdown("---")
        else:
            st.error("No versions found for this dataset")