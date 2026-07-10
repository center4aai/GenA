import streamlit as st
from gena.views import page_subtitle

st.title("Documentation")
st.markdown("<a name='top'></a>", unsafe_allow_html=True)
page_subtitle(
    "Step-by-step guide, chunk gate, weighted validation scoring, sensitivity levels."
)
st.markdown(
    """
- [About GenA](#about-gen-a)
- [Sensitivity levels](#sensitivity-levels)
- [Question types](#question-types)
- [How to use GenA](#how-to-use-gen-a)
- [Chunk gate](#chunk-gate)
- [Chunks storage](#chunks-storage)
- [Validation](#validation)
"""
)
st.markdown("---")

# ── About ──
st.markdown('<a name="about-gen-a"></a>', unsafe_allow_html=True)
st.markdown("""
## About GenA

GenA (Generation & Sensitivity Assessment) is a dynamic Russian-language framework
designed to address the gap between classical question generation (QG) and practical
requirements for legally accurate, socially safe questions, providing users with a
controllable and transparent tool while minimizing conflict risks associated with
sensitive topics (including those arising from cultural, historical, and linguistic
considerations).

The framework provides three key contributions:

1. **Generating diverse questions** from Russian texts (exemplified with socially
   significant texts from trusted sources such as encyclopedias, regulatory legal
   acts, and legal documents).
2. **Automatically assigning sensitivity levels** that reflect conflict potential
   within sociocultural contexts.
3. **Incorporating human-in-the-loop validation** processes. Our dual-validation
   methodology combined with human intervention allows users to evaluate question
   quality, accept or reject generated questions, and validate or modify assigned
   sensitivity levels, thereby fostering continuous improvement and ensuring
   methodological rigor.
""")

# ── Sensitivity levels ──
st.markdown('<a name="sensitivity-levels"></a>', unsafe_allow_html=True)
st.markdown("""
## Sensitivity levels

Each question receives a specialized sensitivity annotation ranging from 1 to 3,
based on the perceived probability of conflict emergence within a given sociocultural
context at a fixed point in time:

| Level | Description |
|-------|-------------|
| **1 — Low** | Neutral topics that do not foster discussion or differences of opinion. |
| **2 — Medium** | Controversial or ambiguous topics. Different viewpoints exist but are not radically opposed. Discussions may lead to lively debates but do not result in serious conflicts. |
| **3 — High** | Highly sensitive cultural, historical, or political topics. Responses may require expressing personal opinions on contentious issues. |

Users can evaluate both question generation quality and sensitivity level
appropriateness, forming a crucial feedback loop for continuous dynamic service
improvement.
""")

# ── Question types ──
st.markdown('<a name="question-types"></a>', unsafe_allow_html=True)
st.markdown("""
## Question types

The generation involves 3 question types:

- **Single-choice (`one`)** — one correct answer is selected from multiple options.
- **Multiple-choice (`multi`)** — several correct answers are selected from the options provided.
- **Open-ended (`open`)** — no options provided.
""")

st.markdown("---")

# ── How to use ──
st.markdown('<a name="how-to-use-gen-a"></a>', unsafe_allow_html=True)
st.markdown("""
## How to use GenA

Follow these steps to generate questions from a document:

### 1. Upload a document

Go to **Data Preprocessing** and click the upload area.
Supported formats: `.docx`, `.txt`, `.pdf`.

The document will be automatically split into logical chunks.
You will see a progress bar and chunk count once it's done.
""")
st.page_link("views/bot.py", label="Open Data Preprocessing", icon=":material/arrow_forward:")

st.markdown("""
### 2. Select question types and configure generation

Choose one or more question types:
- `one` — single correct answer
- `multi` — multiple correct answers
- `open` — open-ended question

> By default all three types are selected. If you clear the selection, it resets
> to all three (you cannot run generation with an empty list).

Additional settings:
- **Model selection** — pick which LLM to use for **generation** (questions,
  sensitivity, difficulty) and which to use for **validation** (chunk gate before
  generation, plus scoring of the generated question after generation).
- **Processing mode:**
  - *Queue Mode (recommended)* — tasks are added to a background queue; you can track progress in Queue Manager.
  - *Direct Processing* — questions are generated immediately on the page (slower for large documents).

### 3. Preview chunks

After upload, you can expand the **Preview chunks** section to inspect the text
fragments the chunker produced. Adjacent chunks share a small overlap (last 1-2
sentences) so no information is lost at boundaries.

### 4. Generate questions

Click **Generate Questions**. In **Queue mode** the system performs these steps
automatically before creating tasks:

1. **Chunk gate** — each chunk is sent to the validation model for a suitability
   check (informative? self-contained? suitable for multi-choice?).  Chunks that
   fail the gate are discarded; no tasks are created for them.
2. **Save chunks** — only chunks that passed the gate are persisted in a separate
   `chunks` collection in the database (linked to the dataset by `dataset_id`).
3. **Create tasks** — one task per valid chunk per selected question type, with
   `chunk_pre_validated=true` so the generation pipeline skips the duplicate gate
   check.

In **Direct Processing** the gate still runs inside the generation pipeline for each
request (as before), but rejected chunks produce empty results.

### 5. Track progress

Switch to **Queue Manager** to monitor:
- Overall queue statistics and task counts
- Per-dataset progress bars with completion percentage
- Model health status — see which models are online or offline
- Failed tasks — view errors and retry with one click
""")
st.page_link("views/queue_manager.py", label="Open Queue Manager", icon=":material/arrow_forward:")

st.markdown("""
### 6. View and edit results

Go to **Results & Editor** to browse generated questions. For each question you will see:
- The **source text** (click to expand)
- The **task** formulation
- A numbered list of **options** (for single/multi)
- The **correct answer**
- The **sensitivity score** (1-3)
- The **difficulty level** (1-3)
- The **validation score** (total / maximum; scores may be fractional decimals) and
  pass/fail status
- Detailed validation breakdown (click to expand)

You can:
- Edit any field inline and save as a new version
- Compare different versions side by side
- Export generation results and full-pipeline data as XLSX

> After viewing the results, click the **Confirm** button to validate the
> sensitivity and provocativeness level for each question.
""")
st.page_link("views/dataset_editor.py", label="Open Results & Editor", icon=":material/arrow_forward:")

st.markdown("""
### 7. Download as XLSX

Once done, use **Download XLSX** in **Results & Editor** to export results (and
optional full pipeline) as a spreadsheet.

### 8. Analyze statistics

The **Statistics** page shows aggregated analytics:
- Question type distribution across datasets
- Sensitivity level distribution
- Difficulty level distribution
- Validation pass rates
- Processing status breakdown
""")
st.page_link("views/statistics.py", label="Open Statistics", icon=":material/arrow_forward:")

st.markdown("---")

# ── Chunk gate ──
st.markdown('<a name="chunk-gate"></a>', unsafe_allow_html=True)
st.markdown("""
## Chunk gate

Before question generation, every chunk is assessed by a **gate** using the validation
model. Three binary criteria are checked:

- **c1 — Informative**: does the chunk contain enough substance for a question?
- **c2 — Reference clarity**: is the chunk understandable without external sources?
- **c3 — Multi suitability** (only for `multi`): can the chunk support plausible wrong answers?

If c1 or c2 fails, the chunk is discarded entirely. If c3 fails, only `multi` tasks
are skipped — `one` and `open` can still proceed.

In **Queue mode**, the gate runs **before** tasks are created: rejected chunks never
enter the queue, and only valid chunks are stored in the database. In **Direct mode**,
the gate runs inside each generation call; rejected chunks produce empty results.
""")

# ── Chunks storage ──
st.markdown('<a name="chunks-storage"></a>', unsafe_allow_html=True)
st.markdown("""
## Chunks storage

Chunks are stored **separately** from datasets in a dedicated `chunks` collection,
linked by `dataset_id`. Each chunk record includes the text, fragment metadata, gate
results, and the list of question types it is valid for. This separation ensures that
dataset queries (metadata, questions, versions) are not bloated by chunk bodies, and
chunks can be re-processed or inspected independently.

The **Existing Datasets** table on the **Data Preprocessing** page shows
the chunk count (valid / total) for each dataset.
""")

# ── Validation ──
st.markdown('<a name="validation"></a>', unsafe_allow_html=True)
st.markdown("""
## Validation

Every **generated** question goes through automated quality validation that checks:
- Factual correctness against the source text
- Answer completeness and consistency
- Question clarity and grammatical quality
- Option plausibility (for single/multi choice)

**Scoring.** Sub-criteria are combined using **weights** (some criteria count half,
others are emphasised). Several criteria act as **critical multipliers**: if any of
them scores zero, the overall total becomes zero. The reported score is therefore a
**decimal** (e.g. `15.5 / 20.5`), not only whole numbers.

The validation produces a numeric score and a pass/fail verdict based on a
configurable threshold (also expressed as a decimal where needed). Experts can then
review, override, and refine the results in the editor.
""")

st.markdown("---")
st.page_link("views/home.py", label="Back to Home", icon=":material/home:")
