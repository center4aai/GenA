import streamlit as st
import os
import base64
from gena.config import LOGO


def get_base64_image(img_path):
    with open(img_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()


# ── Logo ──
if os.path.exists(LOGO):
    img_base64 = get_base64_image(LOGO)
    st.markdown(
        f"""
        <div style="display: flex; justify-content: center;">
            <img src="data:image/png;base64,{img_base64}" width="420"/>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    """
    <h1 style="text-align:center; margin-top:0.2em;">
        GenA 2.0
    </h1>
    <p style="text-align:center; font-size:1.15em; color:#555; margin-bottom:1.5em;">
        Generation &amp; Sensitivity Assessment &mdash; Russian-language framework
        for automated question generation with sensitivity and quality control.
    </p>
    """,
    unsafe_allow_html=True,
)

# ── Quick Start ──
st.markdown("---")
st.markdown("## Quick Start")
st.markdown(
    "Four steps from a document to a ready dataset. "
    "Click a step to go directly to the page."
)

cols = st.columns(4)
steps = [
    ("1. Upload document",   "Upload a .docx / .txt / .pdf and it will be split into chunks automatically."),
    ("2. Choose settings",   "Pick question types (single, multi, open), select LLM model, and hit Generate."),
    ("3. Track progress",    "Watch processing status in real time and see model health."),
    ("4. View results",      "Browse generated questions, edit them, compare versions, and export CSV."),
]
pages = [
    "views/bot.py",
    "views/bot.py",
    "views/queue_manager.py",
    "views/dataset_editor.py",
]

for col, (title, desc), page in zip(cols, steps, pages):
    with col:
        st.markdown(
            f"""
            <div style="
                border:1px solid #ddd; border-radius:12px; padding:1em 0.8em;
                min-height:180px; background:#f9f9f9;
            ">
                <h4 style="margin:0 0 0.4em 0;">{title}</h4>
                <p style="font-size:0.92em; color:#444; margin:0;">{desc}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.page_link(page, label=f"Go to {title.split('. ', 1)[1]}", icon=":material/arrow_forward:")

# ── Pages overview ──
st.markdown("---")
st.markdown("## Pages")

page_info = [
    (":material/quiz:",        "Question Generator",     "views/bot.py",
     "Upload a document, configure generation parameters, run direct processing or queue-based generation."),
    (":material/table_chart:",  "Results & Editor",       "views/dataset_editor.py",
     "Browse generated questions, edit them inline, compare dataset versions, export to CSV."),
    (":material/queue:",        "Queue Manager",          "views/queue_manager.py",
     "Monitor task queues and dataset progress, see model health, retry failed tasks."),
    (":material/bar_chart:",    "Statistics",             "views/statistics.py",
     "Aggregated analytics across datasets: question type distribution, sensitivity levels, validation rates."),
    (":material/shuffle:",      "Dynamic Implementation", "views/dynamic_implementation.py",
     "Shuffle and rephrase questions using LLM to create alternative dataset variants."),
    (":material/menu_book:",    "Documentation",          "views/docs.py",
     "Step-by-step guide, sensitivity levels explanation, validation details."),
]

for icon, title, page, desc in page_info:
    col_link, col_desc = st.columns([1, 3])
    with col_link:
        st.page_link(page, label=f"**{title}**", icon=icon)
    with col_desc:
        st.caption(desc)

# ── Documentation link ──
st.markdown("---")
st.page_link("views/docs.py", label="Full documentation", icon=":material/menu_book:")

# ── About (collapsible) ──
with st.expander("About GenA", expanded=False):
    st.markdown("""
**GenA** (Generation & Sensitivity Assessment) is a dynamic Russian-language framework
designed to bridge the gap between classical question generation and practical
requirements for legally accurate, socially safe questions.

**Three question types:** single-choice, multiple-choice, open-ended.

**Sensitivity levels (1-3):**
- **Level 1** — neutral topics, no potential for conflict.
- **Level 2** — controversial topics with differing but non-extreme viewpoints.
- **Level 3** — highly sensitive cultural, historical, or political topics.

Each question goes through automated validation that assesses quality across
multiple criteria. Experts can then review, edit, and approve the results.
""")
