from datetime import datetime
from collections import defaultdict

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from gena.http import get
from gena.config import API_DATASET_URL
from gena.views import page_subtitle


def _parse_validation_score(score_str):
    """'16.0/16.0' -> (16.0, 16.0); '17/21' -> (17.0, 21.0); иначе -> (None, None)"""
    try:
        if not score_str:
            return None, None
        parts = str(score_str).split("/")
        if len(parts) != 2:
            return None, None
        return float(parts[0]), float(parts[1])
    except Exception:
        return None, None

def _to_bool(val):
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("true", "passed", "yes", "1"):
            return True
        if v in ("false", "failed", "no", "0"):
            return False
    return None

def _is_passed(q: dict):
    """
    Определяем passed: сначала по validation_passed (если оно есть и понятно),
    иначе по total >= threshold, если оба есть.
    """
    vp = _to_bool(q.get("validation_passed"))
    if vp is not None:
        return vp
    total, _max_total = _parse_validation_score(q.get("validation_score"))
    try:
        thr = float(q.get("validation_threshold")) if q.get("validation_threshold") not in (None, "N/A", "") else None
    except Exception:
        thr = None
    if total is not None and thr is not None:
        return total >= thr
    return None

# ---------- API ----------
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

# ---------- UI ----------
st.title("Statistics")
st.markdown("<a name='top'></a>", unsafe_allow_html=True)

page_subtitle(
    "Aggregated analytics across datasets: question types, sensitivity, difficulty, validation rates."
)
st.markdown(
    """
- [Dataset Status & Progress](#dataset-status-and-progress)
- [Validation Threshold Analysis](#validation-threshold-analysis)
- [Distributions](#distributions)
- [Detailed Dataset Statistics](#detailed-dataset-statistics)
- [Dataset Creation Timeline](#dataset-creation-timeline)
- [Export Statistics](#export-statistics)
"""
)
st.markdown("---")

# ---------- load data ----------
st.markdown("### 📈 Overall Statistics")

datasets = load_datasets()

if not datasets:
    st.info("No datasets found. Generate some datasets first using Data Preprocessing.")
else:
    all_questions = []
    dataset_stats = []

    for d in datasets:
        ds = load_dataset(d["_id"])
        if not ds:
            continue

        questions = ds.get("questions", []) or []
        metadata = ds.get("metadata", {}) or {}

        for q in questions:
            q["dataset_name"] = ds["name"]
            q["dataset_id"] = d["_id"]
            q["created_at"] = ds.get("created_at", "")
            q["dataset_status"] = metadata.get("status", "unknown")
            all_questions.append(q)

        total_questions = len(questions)

        validation_passed_cnt = 0
        considered_for_validation = 0
        for q in questions:
            p = _is_passed(q)
            if p is not None:
                considered_for_validation += 1
                if p:
                    validation_passed_cnt += 1

        total_chunks = metadata.get("total_chunks", 0)
        question_types = metadata.get("question_types", [])
        questions_per_chunk = len(question_types) if question_types else 1
        # Expected questions = chunks that *actually* enter generation (gate-passed)
        # × number of question types per chunk.  Falling back to ``total_chunks``
        # would over-count when some chunks were rejected by the gate, which
        # makes a finished dataset appear stuck below 100%.
        chunks_for_expected = metadata.get("chunks_passed_gate")
        if chunks_for_expected in (None, 0):
            chunks_for_expected = total_chunks
        expected_questions = (
            chunks_for_expected * questions_per_chunk
            if chunks_for_expected
            else total_questions
        )
        total_questions_generated = metadata.get("total_questions_generated", total_questions)
        ds_status = metadata.get("status", "unknown")
        if ds_status == "completed":
            # By definition a completed dataset has produced everything it was
            # going to produce — show 100% rather than a stale ratio that would
            # otherwise dip when gate-rejected chunks are counted in the
            # denominator.
            progress_percent = 100.0
        else:
            progress_percent = (
                total_questions_generated / expected_questions * 100
                if expected_questions
                else 0
            )

        dataset_stats.append({
            "dataset_name": ds["name"],
            "dataset_id": d["_id"],
            "status": metadata.get("status", "unknown"),
            "total_questions": total_questions,
            "expected_questions": expected_questions,
            "total_questions_generated": total_questions_generated,
            "progress_percent": progress_percent,
            "validation_rate": (validation_passed_cnt / considered_for_validation * 100) if considered_for_validation else 0,
            "created_at": ds.get("created_at", ""),
            "last_updated": metadata.get("last_updated", ""),
        })

    if not dataset_stats:
        st.info("No questions found in datasets. Generate some questions first.")
        st.stop()

    df_ds = pd.DataFrame(dataset_stats)

    # ---------- top metrics ----------
    total_questions_all = sum(x.get("total_questions", 0) for x in dataset_stats)

    overall_passed = 0
    overall_considered = 0
    for q in all_questions:
        p = _is_passed(q)
        if p is not None:
            overall_considered += 1
            if p:
                overall_passed += 1
    overall_val_rate = (overall_passed / overall_considered * 100) if overall_considered else 0.0

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total Datasets", len(datasets))
    with col2:
        st.metric("Total Questions", total_questions_all)
    with col3:
        completed = (df_ds["status"] == "completed").sum()
        processing = (df_ds["status"] == "processing").sum()
        st.markdown(
            "<div style='color:#808495;font-size:0.875rem'>Status</div>"
            "<div style='font-size:1.875rem;line-height:2.25rem;white-space:normal;"
            f"word-break:break-word;'>{completed} completed, {processing} processing</div>",
            unsafe_allow_html=True,
        )
    with col4:
        avg_progress = df_ds["progress_percent"].mean() if not df_ds.empty else 0.0
        st.metric("Average Progress", f"{avg_progress:.1f}%")
    with col5:
        st.metric("Avg. Validation Rate", f"{overall_val_rate:.1f}%")

    st.markdown("---")

    # ---------- Dataset status & progress ----------
    st.markdown('<a name="dataset-status-and-progress"></a>', unsafe_allow_html=True)
    st.markdown("#### 📊 Dataset Status & Progress")

    c1, c2 = st.columns(2)

    with c1:
        status_counts = df_ds["status"].value_counts()
        if not status_counts.empty:
            fig = px.pie(values=status_counts.values,
                         names=status_counts.index,
                         title="Dataset Processing Status")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No status data.")

    with c2:
        if not df_ds.empty:
            df_prog = df_ds.copy()
            df_prog["Completed"] = df_prog["progress_percent"].clip(0, 100)
            df_prog["Remaining"] = (100 - df_prog["Completed"]).clip(0, 100)

            fig = go.Figure()
            fig.add_trace(go.Bar(
                y=df_prog["dataset_name"],
                x=df_prog["Completed"],
                orientation="h",
                name="Completed (%)",
                text=[f"{v:.1f}%" for v in df_prog["Completed"]],
                textposition="inside"
            ))
            fig.add_trace(go.Bar(
                y=df_prog["dataset_name"],
                x=df_prog["Remaining"],
                orientation="h",
                name="Remaining (%)",
                text=[f"{v:.1f}%" for v in df_prog["Remaining"]],
                textposition="inside"
            ))
            fig.update_layout(
                barmode="stack",
                title="Processing Progress by Dataset",
                xaxis_title="Percent",
                yaxis_title=None,
                xaxis=dict(range=[0, 100]),
                legend_title=None
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No progress data.")

    # ---------- Validation Score & Threshold Analysis [NEW] ----------
    score_rows = []
    for q in all_questions:
        total, max_total = _parse_validation_score(q.get("validation_score"))
        if total is not None:
            score_rows.append({"score": total})

    threshold_rows = []
    for q in all_questions:
        thr = q.get("validation_threshold", "N/A")
        if thr in (None, "", "N/A"):
            continue
        try:
            thr = int(thr)
        except Exception:
            continue
        grp = "Test (one/multi)" if q.get("question_type") in ("one", "multi") else "Open"
        passed = _is_passed(q)
        threshold_rows.append({"threshold": thr, "group": grp, "passed": bool(passed)})

    if score_rows or threshold_rows:
        st.markdown("---")
        st.markdown('<a name="validation-threshold-analysis"></a>', unsafe_allow_html=True)
        st.markdown("#### 📊 Validation Threshold Analysis")

        col_a, col_b = st.columns(2)

        # Левый график — распределение по набранным баллам
        with col_a:
            if score_rows:
                score_df = pd.DataFrame(score_rows)
                score_counts = score_df["score"].value_counts().sort_index()
                df_scores = score_counts.rename_axis("Score").reset_index(name="Count")

                fig = px.bar(
                    df_scores,
                    x="Score",
                    y="Count",
                    title="Distribution of Validation Scores",
                    text="Count"
                )
                fig.update_traces(marker_line_width=1, marker_line_color="white")
                fig.update_layout(
                    xaxis=dict(dtick=1),  
                    bargap=0.2             
                )
                st.plotly_chart(fig, use_container_width=True)

        # Правый график — доля успешных по threshold
        with col_b:
            if threshold_rows:
                thr_df = pd.DataFrame(threshold_rows)
                sr = thr_df.groupby("threshold")["passed"].agg(["count", "sum"]).reset_index()
                sr["success_rate"] = sr.apply(lambda r: (r["sum"] / r["count"] * 100) if r["count"] else 0.0, axis=1)

                fig = px.line(
                    sr,
                    x="threshold",
                    y="success_rate",
                    markers=True,
                    title="Success Rate by Threshold",
                    labels={"threshold": "Threshold", "success_rate": "Success Rate (%)"}
                )
                fig.update_yaxes(range=[0, 100])
                fig.update_xaxes(dtick=1)
                st.plotly_chart(fig, use_container_width=True)

    # ---------- Distributions ----------
    if all_questions:
        st.markdown("---")
        st.markdown('<a name="distributions"></a>', unsafe_allow_html=True)
        st.markdown("#### 📊 Distributions")

        q_df = pd.DataFrame(all_questions)
        colx, coly, colz  = st.columns(3)

        with colx:
            prov_series = (
                q_df["provocativeness"]
                .dropna()
                .astype(str)
                .map({"1": "Low (1)", "2": "Medium (2)", "3": "High (3)"})
                .value_counts()
                .reindex(["Low (1)", "Medium (2)", "High (3)"])
                .fillna(0)
            )
            prov_df = prov_series.rename_axis("Provocativeness").reset_index(name="Count")
            fig = px.bar(prov_df, x="Provocativeness", y="Count", title="Provocativeness Distribution")
            st.plotly_chart(fig, use_container_width=True)

        with colz:
            if "difficulty" in q_df.columns:
                diff_series = (
                    q_df["difficulty"]
                    .dropna()
                    .astype(str)
                    .map({"1": "Easy (1)", "2": "Medium (2)", "3": "Hard (3)"})
                    .value_counts()
                    .reindex(["Easy (1)", "Medium (2)", "Hard (3)"])
                    .fillna(0)
                )
                diff_df = diff_series.rename_axis("Difficulty").reset_index(name="Count")
                fig = px.bar(diff_df, x="Difficulty", y="Count", title="Difficulty Distribution")
                st.plotly_chart(fig, use_container_width=True)

        with coly:
            type_map = {"one": "Single Choice", "multi": "Multiple Choice", "open": "Open Ended"}
            type_series = q_df["question_type"].map(type_map).fillna("Unknown").value_counts()
            type_df = type_series.rename_axis("Question Type").reset_index(name="Count")
            fig = px.bar(type_df, x="Question Type", y="Count", title="Question Type Distribution")
            st.plotly_chart(fig, use_container_width=True)


    # ---------- Detailed table ----------
    st.markdown("---")
    st.markdown('<a name="detailed-dataset-statistics"></a>', unsafe_allow_html=True)
    st.markdown("#### 📋 Detailed Dataset Statistics")

    display_rows = []
    for _, r in df_ds.iterrows():
        display_rows.append({
            "Dataset Name": r["dataset_name"],
            "Status": r["status"],
            "Progress": f"{r['progress_percent']:.1f}%",
            "Questions Generated": r["total_questions_generated"],
            "Expected Questions": r["expected_questions"],
            "Validation Rate (%)": f"{r['validation_rate']:.1f}%",
            "Created": r["created_at"][:19] if r["created_at"] else "N/A",
            "Last Updated": r["last_updated"][:19] if r["last_updated"] else "N/A",
        })
    st.dataframe(pd.DataFrame(display_rows), use_container_width=True)

    # ---------- Timeline ----------
    st.markdown("---")
    st.markdown('<a name="dataset-creation-timeline"></a>', unsafe_allow_html=True)
    st.markdown("#### 📅 Dataset Creation Timeline")

    tl_rows = []
    for _, r in df_ds.iterrows():
        if r["created_at"]:
            try:
                dt = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
                tl_rows.append({"Date": dt.date(), "Questions": r["total_questions_generated"], "Dataset": r["dataset_name"]})
            except Exception:
                pass
    if tl_rows:
        tl_df = pd.DataFrame(tl_rows).groupby("Date").agg({"Questions": "sum", "Dataset": "count"}).reset_index()
        tl_df.columns = ["Date", "Total Questions", "Datasets Created"]
        fig = px.line(tl_df, x="Date", y="Total Questions", title="Questions Generated Over Time")
        st.plotly_chart(fig, use_container_width=True)

    # ---------- Export ----------
    st.markdown("---")
    st.markdown('<a name="export-statistics"></a>', unsafe_allow_html=True)
    st.markdown("#### 📥 Export Statistics")

    cexp1, cexp2 = st.columns(2)
    with cexp1:
        csv_datasets = df_ds.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Download Dataset Statistics (CSV)",
            data=csv_datasets,
            file_name="dataset_statistics.csv",
            mime="text/csv",
        )
    with cexp2:
        if all_questions:
            df_q = pd.DataFrame(all_questions)
            st.download_button(
                "📥 Download All Questions (CSV)",
                data=df_q.to_csv(index=False).encode("utf-8"),
                file_name="all_questions.csv",
                mime="text/csv",
            )

# ---------- Auto-refresh ----------
if st.checkbox("🔄 Auto-refresh every 60 seconds", key="stats_auto_refresh"):
    import time
    time.sleep(60)
    st.rerun()

st.markdown("---")
st.markdown("[⬆️ Back to top](#top)")