import streamlit as st
from datetime import datetime

from gena.views.dataset_editor import (
    load_datasets,
    load_dataset_versions,
    load_dataset,
    update_dataset,
    _apply_cache_defaults,
    _ensure_edit_cache,
    _save_current_page_draft,
    _k,  
)
from gena.controllers.dynamic_implementation_controller import shuffle_questions, rephrase_questions


def _norm_options(opt):
    """Преобразует options (dict или str) в читаемый многострочный текст."""
    if opt is None:
        return "—"
    if isinstance(opt, dict):
        lines = []
        for k, v in opt.items():
            if v not in (None, "", "None"):
                lines.append(f"{k}: {v}")
        return "\n".join(lines) if lines else "—"
    elif isinstance(opt, str):
        return opt.strip() if opt.strip() else "—"
    else:
        return str(opt) if opt else "—"


def clear_question_form_cache():
    """
    Очищает session_state от ключей, связанных с формой редактирования вопросов.
    """
    keys_to_delete = []
    for k in list(st.session_state.keys()):
        if k.startswith(("task_", "options_", "answer_", "type_", "prov_", "diff_")):
            keys_to_delete.append(k)
        # контекстные ключи вида "<ctx>::type_1"
        if any(seg in k for seg in ("::task_", "::options_", "::answer_", "::type_", "::prov_", "::diff_")):
            keys_to_delete.append(k)
    for k in set(keys_to_delete):
        del st.session_state[k]
    st.session_state.pop("edits", None)


is_expert = st.session_state.get("role") == "expert"

st.title("📊 Available Datasets")

# Загрузка датасетов
datasets = load_datasets()
if not datasets:
    st.info("No datasets found. Generate some datasets first using the Question Generator.")
    st.stop()

# === Callbacks для сброса состояния при смене выбора ===
def on_dataset_change():
    st.session_state.current_dataset = None
    st.session_state.selected_version = None  # сброс версии при новом датасете
    clear_question_form_cache()

def on_version_change():
    st.session_state.current_dataset = None
    clear_question_form_cache()

# === Выбор датасета ===
dataset_options = {f"{d['name']} (v{d['current_version']})": d['_id'] for d in datasets}
selected_dataset_name = st.selectbox(
    "Select a dataset:",
    options=list(dataset_options.keys()),
    key="dataset_selector",
    on_change=on_dataset_change
)
if not selected_dataset_name:
    st.info("No dataset selected.")
    st.stop()

selected_dataset_id = dataset_options[selected_dataset_name]

# === Загрузка и выбор версии ===
dataset_versions = load_dataset_versions(selected_dataset_id)
if not dataset_versions:
    st.error("No dataset versions found for this dataset")
    st.stop()

version_options = {f"Version {v['version']} ({v['created_at'][:10]})": v['version'] for v in dataset_versions}
selected_version_name = st.selectbox(
    "Select version:",
    options=list(version_options.keys()),
    key="version_selector",
    on_change=on_version_change
)
selected_version = version_options[selected_version_name]

_ctx = f"{selected_dataset_id}:{selected_version}"

# === Инициализация состояния ===
if "shuffle_mode" not in st.session_state:
    st.session_state.shuffle_mode = False
if "rephrase_mode" not in st.session_state:
    st.session_state.rephrase_mode = False

# === Загрузка датасета, если ещё не загружен ===
if st.session_state.get("current_dataset") is None:
    dataset = load_dataset(selected_dataset_id, selected_version)
    if not dataset:
        st.error("❌ Failed to load dataset.")
        st.stop()
    st.session_state.current_dataset = dataset
    st.session_state.selected_dataset_id = selected_dataset_id
    st.session_state.selected_version = selected_version

# === Опции преобразования ===
st.subheader("Choose options")

col_a, col_b = st.columns(2)
with col_a:
    shuffle_mode = st.checkbox("Shuffle the response options", value=st.session_state.shuffle_mode)
with col_b:
    rephrase_mode = st.checkbox("Rephrase the questions", value=st.session_state.rephrase_mode)

st.session_state.shuffle_mode = shuffle_mode
st.session_state.rephrase_mode = rephrase_mode

# === Применение преобразований ===
if st.button("Go"):
    if not (shuffle_mode or rephrase_mode):
        st.error("Please choose at least one mode!")
        st.stop()

    dataset = st.session_state.current_dataset
    questions = dataset.get("questions", []).copy()

    if rephrase_mode:
        questions_info = rephrase_questions(selected_dataset_name, questions)
        if questions_info['status'] != 'success':
            st.info('Sorry! Questions rephrased badly!')
            st.stop()
        questions = questions_info['result']
        st.success("✅ Questions rephrased successfully!")

    if shuffle_mode:
        questions = shuffle_questions(questions)
        st.success("✅ Questions shuffled successfully!")

    # Обновляем вопросы в сессии
    st.session_state.current_dataset["questions"] = questions

    # Сбрасываем кэш формы и страницу
    clear_question_form_cache()
    st.session_state.page = 1

    # Перезапуск для отображения новых вопросов
    st.rerun()

# === Отображение вопросов ===
questions = st.session_state.current_dataset.get("questions", [])
if not questions:
    st.info("No questions in this dataset.")
    st.stop()

st.markdown("---")
st.markdown("### ❓ Questions")

page_size = 10
total_questions = len(questions)
num_pages = (total_questions - 1) // page_size + 1 if total_questions > 0 else 1

if "page" not in st.session_state:
    st.session_state.page = 1
st.session_state.page = max(1, min(st.session_state.page, num_pages))

st.session_state.page = st.number_input(
    "Page", min_value=1, max_value=num_pages, value=st.session_state.page, step=1,
    key=f"page_selector::{_ctx}"
)

start = (st.session_state.page - 1) * page_size
end = start + page_size
page_questions = questions[start:end]

# === Форма редактирования (только для экспертов) ===
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
                    f"Type {i}:", qtype_list, index=qtype_idx, key=_k(_ctx, "type", i)
                )
            with col2:
                # Provocativeness
                prov_list = ['1', '2', '3']
                prov_val = str(st.session_state.get(_k(_ctx, "prov", i), str(question.get('provocativeness', '2'))))
                prov_idx = prov_list.index(prov_val) if prov_val in prov_list else 1
                provocativeness = st.selectbox(
                    f"Provocativeness {i}:", prov_list, index=prov_idx, key=_k(_ctx, "prov", i)
                )

                diff_list = ['1', '2', '3']
                diff_val = str(st.session_state.get(_k(_ctx, "diff", i), str(question.get('difficulty', '2'))))
                diff_idx = diff_list.index(diff_val) if diff_val in diff_list else 1
                difficulty = st.selectbox(
                    f"Difficulty {i}:", diff_list, index=diff_idx, key=_k(_ctx, "diff", i)
                )

            task = st.text_area(
                f"Task {i}:",
                value=st.session_state.get(_k(_ctx, "task", i), question.get('task', '')),
                height=100,
                key=_k(_ctx, "task", i),
            )

            options_value = st.session_state.get(_k(_ctx, "options", i))
            if options_value is None:
                ov = question.get('options', '')
                if isinstance(ov, dict):
                    options_text = "\n".join(f"{k}: {v}" for k, v in ov.items() if v and v != "None")
                else:
                    options_text = str(ov or "")
            else:
                options_text = options_value

            options = st.text_area(
                f"Options {i}:", value=options_text, height=80, key=_k(_ctx, "options", i)
            )

            correct_answer = st.text_area(
                f"Correct Answer {i}:",
                value=st.session_state.get(_k(_ctx, "answer", i), str(question.get('correct_answer', ''))),
                height=60,
                key=_k(_ctx, "answer", i),
            )

            edited_question = {
                "question_id": question.get('question_id'),
                "chunk_id": question.get('chunk_id'),
                "question_type": question_type,
                "task": task,
                "options": options,
                "correct_answer": correct_answer,
                "provocativeness": provocativeness,
                "difficulty": difficulty,
            }
            for k in ("validation_passed", "validation_score", "validation_threshold", "validation_details", "source_chunk"):
                if question.get(k) is not None:
                    edited_question[k] = question[k]

            edited_questions.append(edited_question)
            st.markdown("---")

        st.form_submit_button("Review Changes (no auto-save)")

else:
    # Режим только для чтения
    for i, question in enumerate(page_questions, start=start + 1):
        st.markdown(f"#### Question {i} (Chunk {question.get('chunk_id','—')})")
        if question.get('source_chunk'):
            with st.expander("📄 View Source Text", expanded=False):
                st.markdown("**Source Text:**")
                st.markdown(f"```\n{question['source_chunk']}\n```")
        st.write(f"**Type:** {question.get('question_type','—')}")
        st.write(f"**Provocativeness:** {question.get('provocativeness','—')}")
        st.write(f"**Difficulty:** {question.get('difficulty','—')}")
        st.write(f"**Task:** {question.get('task','—')}")
        st.write("**Options:**")
        st.text(_norm_options(question.get('options')))
        st.write(f"**Correct Answer:** {question.get('correct_answer','—')}")
        st.markdown("---")

# === Навигация ===
col1, col2, col3 = st.columns([1, 1, 1])
with col1:
    if st.session_state.page > 1:
        if st.button("⬅️ Prev"):
            if is_expert:
                _save_current_page_draft(_ctx, page_questions, start)
            st.session_state.page -= 1
            st.rerun()
with col2:
    _ensure_edit_cache()
    edits_count = len(st.session_state.get("edits", {}))
    st.markdown(
        f"**Page {st.session_state.page}/{num_pages}**  &nbsp;&nbsp; _draft edits: {edits_count}_",
        unsafe_allow_html=True
    )
with col3:
    if st.session_state.page < num_pages:
        if st.button("Next ➡️"):
            if is_expert:
                _save_current_page_draft(_ctx, page_questions, start)  # передаём ctx
            st.session_state.page += 1
            st.rerun()

# === Сохранение новой версии (только для экспертов) ===
if is_expert:
    if st.button("💾 Save Changes as New Version"):
        _save_current_page_draft(_ctx, page_questions, start)  # передаём ctx

        _ensure_edit_cache()
        original_questions = st.session_state.current_dataset["questions"]
        updated_questions = []

        for idx, q in enumerate(original_questions):
            if idx in st.session_state.edits:
                updated_questions.append(st.session_state.edits[idx])
            else:
                passthrough = {
                    "question_id": q.get('question_id'),
                    "chunk_id": q.get('chunk_id'),
                    "question_type": q.get('question_type', ''),
                    "task": q.get('task', ''),
                    "options": q.get('options', ''),
                    "correct_answer": str(q.get('correct_answer', '')),
                    "provocativeness": str(q.get('provocativeness', '')),
                    "difficulty": str(q.get('difficulty', ''))
                }
                for k in ("validation_passed", "validation_score", "validation_threshold", "validation_details", "source_chunk"):
                    if q.get(k) is not None:
                        passthrough[k] = q[k]
                updated_questions.append(passthrough)

        metadata = st.session_state.current_dataset.get('metadata', {}) or {}
        metadata.update({
            'edited_at': datetime.now().isoformat(),
            'edited_by': st.session_state.get('username', 'expert'),
            'source_version': selected_version,
        })

        result = update_dataset(
            dataset_id=selected_dataset_id,
            questions_data=updated_questions,
            metadata=metadata
        )

        if result:
            st.success(f"✅ Dataset updated successfully! New version: {result['new_version']}")
            st.session_state.pop("edits", None)
            st.rerun()
        else:
            st.error("❌ Failed to save new version. Check logs or try again.")
else:
    st.info("🔒 Read-only: You are logged in as a regular user. Editing is available for experts only.")