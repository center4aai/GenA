import streamlit as st

st.title("Documentation")

st.markdown("---")

# ── About ──
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
st.markdown("""
## Question types

The generation involves 3 question types:

- **Single-choice (`one`)** — one correct answer is selected from multiple options.
- **Multiple-choice (`multi`)** — several correct answers are selected from the options provided.
- **Open-ended (`open`)** — no options provided.
""")

st.markdown("---")

# ── How to use ──
st.markdown("""
## How to use GenA

Follow these steps to generate questions from a document:

### 1. Upload a document

Go to **Question Generator** and click the upload area.
Supported formats: `.docx`, `.txt`, `.pdf`.

The document will be automatically split into logical chunks.
You will see a confirmation message with the number of chunks once it's done.
""")
st.page_link("views/bot.py", label="Open Question Generator", icon=":material/arrow_forward:")

st.markdown("""
### 2. Select question types and configure generation

Choose one or more question types:
- `one` — single correct answer
- `multi` — multiple correct answers
- `open` — open-ended question

> If no types are selected, all of them will be used by default.

Additional settings:
- **Model selection** — pick which LLM model to use for generation and validation.
- **Processing mode:**
  - *Queue Mode (recommended)* — tasks are added to a background queue; you can track progress in Queue Manager.
  - *Direct Processing* — questions are generated immediately on the page (slower for large documents).

### 3. Generate questions

Click the **Generate Questions** button to start generating tasks for all chunks
in the document. For each chunk, one question of each selected type will be generated.

### 4. Track progress

Switch to **Queue Manager** to monitor:
- Overall queue statistics and task counts
- Per-dataset progress bars with completion percentage
- Model health status — see which models are online or offline
- Failed tasks — view errors and retry with one click
""")
st.page_link("views/queue_manager.py", label="Open Queue Manager", icon=":material/arrow_forward:")

st.markdown("""
### 5. View and edit results

Go to **Results & Editor** to browse generated questions. For each question you will see:
- The **source text** (click to expand)
- The **task** formulation
- A numbered list of **options** (for single/multi)
- The **correct answer**
- The **sensitivity score** (1-3)
- The **difficulty level** (1-3)
- The **validation score** and pass/fail status
- Detailed validation breakdown (click to expand)

You can:
- Edit any field inline and save as a new version
- Compare different versions side by side
- Export the full dataset as CSV

> After viewing the results, click the **Confirm** button to validate the
> sensitivity and provocativeness level for each question.
""")
st.page_link("views/dataset_editor.py", label="Open Results & Editor", icon=":material/arrow_forward:")

st.markdown("""
### 6. Download as CSV

Once done, click the **Download as CSV** button to export all results as a spreadsheet.

### 7. Analyze statistics

The **Statistics** page shows aggregated analytics:
- Question type distribution across datasets
- Sensitivity level distribution
- Difficulty level distribution
- Validation pass rates
- Processing status breakdown
""")
st.page_link("views/statistics.py", label="Open Statistics", icon=":material/arrow_forward:")

st.markdown("---")

# ── Validation ──
st.markdown("""
## Validation

Every generated question goes through automated quality validation that checks:
- Factual correctness against the source text
- Answer completeness and consistency
- Question clarity and grammatical quality
- Option plausibility (for single/multi choice)

The validation produces a numeric score and a pass/fail verdict based on a
configurable threshold. Experts can then review, override, and refine the results
in the editor.
""")

st.markdown("---")
st.page_link("views/home.py", label="Back to Home", icon=":material/home:")
