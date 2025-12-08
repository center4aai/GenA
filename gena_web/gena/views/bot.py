import streamlit as st
import os
import requests
import random
import pandas as pd
import tempfile
from PIL import Image
import base64
from datetime import datetime

from gena.config import API_CHANKS_URL, CHUNKS_DIR, API_GEN_QUE_URL, API_DATASET_URL, DOCS_DIR, LOGO
from gena.http import get, post, put, delete

def get_base64_image(img_path):
    with open(img_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

if CHUNKS_DIR:
    os.makedirs(CHUNKS_DIR, exist_ok=True)

st.title("GENA 2.0: Generation & sensitivity Assessment Russian language Framework")

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
## 🐊 About GenA:
GenA (Generation & sensitivity Assessment) is a dynamic Russian-language framework designed to address the gap between classical question generation (QG) and practical requirements for legally accurate, socially safe questions, providing users with a controllable and transparent tool while minimizing conflict risks associated with sensitive topics (including those arising from cultural, historical, and linguistic considerations).

The framework provides three key contributions: first, generating diverse questions from Russian texts (exemplified with socially significant texts from trusted sources such as encyclopedias, regulatory legal acts, and legal documents); second, automatically assigning each question sensitivity levels that reflects conflict potential within sociocultural contexts, and third, incorporating human-in-the-loop validation processes. Our dual-validation methodology for sensitivity assessment combined with human intervention allows users to evaluate question quality, accept or reject generated questions, and validate or modify assigned sensitivity levels, thereby fostering continuous improvement and ensuring methodological rigor.

The generation involved 3 question types:
- single-choice, where one correct answer is selected from multiple options;
- multiple-choice, where several correct answers are selected from the options provided;
- open-ended with no options provided.

Consequently, each question receives specialized sensitivity annotation ranging from 1 to 3, based on the perceived probability of conflict emergence within a given sociocultural context at a fixed point in time and reflects the sensitivity level that a particular topic presents to respondents: 
- Low-sensitivity questions (Level 1) pertain to neutral topics that do not foster discussion or differences of opinion;
- Medium-sensitivity questions (Level 2) may address controversial or ambiguous topics. While different viewpoints exist, they are not radically opposed. Discussions may lead to lively debates but do not result in serious conflicts;
- High-sensitivity questions (Level 3) address highly sensitive cultural, historical, or political topics. Responses to such questions may require expressing personal opinions on contentious issues.

Users can evaluate both question generation quality and sensitivity level appropriateness, forming a crucial feedback loop for continuous dynamic service improvement.
            """)
st.markdown("---")

st.markdown("""

## 📘 How to use GenA:

Follow these steps to generate questions from a document:

1. **Upload a document**  
   Click the "Upload a document" button and select a `.docx`, `.txt`, or `.pdf` file from your computer.

2. **Wait for processing**  
   The document will be split into logical chunks. You’ll see a confirmation message once it’s done.

3. **Select question types**  
   Choose one or more question types from the following options:
   - `one` – Single correct answer
   - `multi` – Multiple correct answers
   - `open` – Open-ended question

   > If no types are selected, all of them will be used by default.

4. **Generate questions**  
   Click the **Generate Questions** button to start generating tasks for all chunks in the document. For each chunk, one question of each selected type will be generated.

5. **View the results**  
   For each chunk, you'll see:
   - The **source text** (click to expand)
   - The **task**
   - A numbered list of **options**
   - The **Correct Answer**
   - The **Provocativeness Score**
   - The **Validation Score** (quality assessment)
   - Detailed validation breakdown (click to expand)

> After viewing the results, please click the **Confirm** button to validate the sensitivity and provocativeness level for each question.


6. **Download as CSV**  
   Once done, click the “📥 Download as CSV” button to export all results as a spreadsheet.
""")


st.markdown("---")
st.markdown("### 📄 Document template file for example")
example_path = os.path.join(DOCS_DIR, "Family_code_Russian_Federation_1-4.docx")
if os.path.exists(example_path):
    with open(example_path, "rb") as f:
        st.download_button(
            label="📥 Download document template file",
            data=f,
            file_name="Family_code_Russian_Federation_1-4.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="template_download"
        )
else:
    st.warning("Файл шаблона не найден.")

st.markdown(" ")
st.markdown("---")
st.markdown("### ⬆️ Upload your document for start generation")


#Логика загрузки и обработки документа
uploaded_file = st.file_uploader("Upload a document", type=['docx', 'txt', 'pdf'])

if uploaded_file is not None:
    st.success(f"File uploaded: {uploaded_file.name}")

    dataset_name = st.text_input("Dataset Name:", value=f"Dataset_{uploaded_file.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    dataset_description = st.text_area("Dataset Description (optional):", value=f"Generated from {uploaded_file.name}")
    st.markdown("---")

    with tempfile.NamedTemporaryFile(delete=False, suffix=uploaded_file.name.split('.')[-1]) as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name

    if not API_CHANKS_URL:
        st.error("⛔ API_CHANKS_URL is not configured. Please check your environment variables.")
        st.stop()
        
    with open(tmp_path, "rb") as f:
        files = {"file": (uploaded_file.name, f, uploaded_file.type)}
        try:
            response = requests.post(API_CHANKS_URL, files=files)
            
        except requests.exceptions.ConnectionError:
            st.error("⛔ Could not connect to the FastAPI server. Make sure it is running.")
            st.stop()

    os.remove(tmp_path)
    if response.status_code == 200:
        data = response.json()
        st.success(f"✅ Document successfully splited into chunks. Number of chunks: {data['num_chunks']}")
        
        # Извлекаем document_name из document_type для использования в source_document
        document_name = None
        if "document_type" in data and isinstance(data["document_type"], dict):
            document_name = data["document_type"].get("document_name")
        
        # Если document_name не найден, используем имя файла как fallback
        if not document_name:
            document_name = uploaded_file.name
    else:
        st.error(f"Server error: {response.status_code}")
        st.json(response.text)
        document_name = uploaded_file.name  # Fallback на имя файла
    
    question_types = st.multiselect(
        "Select question types to generate:",
        ['one', 'multi', 'open']
    )
    
    if not question_types:
        question_types = ['one', 'multi', 'open']

    # Выбор режима работы
    processing_mode = st.radio(
        "Processing Mode:",
        ["Queue Mode (Recommended)", "Direct Processing"],
        help="Queue Mode: Add tasks to queue for background processing. Direct Processing: Process immediately (may be slower)."
    )
    
    if st.button("Generate Questions"):
        chunks = data.get("chunks", [])
        total_chunks = len(chunks)
        
        if total_chunks == 0:
            st.error("No chunks found in the document.")
            st.stop()
        
        total_questions = len(chunks) * len(question_types)
        
        # Инициализируем results для обоих режимов
        results = []
        
        if processing_mode == "Queue Mode (Recommended)":
            st.info(f"📊 Adding {total_chunks} chunks × {len(question_types)} question types = {total_questions} total tasks to queue...")
            
            # Создаем очередь для этого датасета
            queue_name = f"queue_{dataset_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            try:
                # Создаем очередь
                queue_payload = {
                    "name": queue_name,
                    "description": f"Queue for {dataset_name}",
                    "priority": 1
                }
                
                queue_response = post("/queues/", json=queue_payload)
                
                if queue_response.status_code != 200:
                    st.error(f"Failed to create queue: {queue_response.status_code}")
                    st.stop()
                
                # Создаем датасет сразу при создании очереди
                dataset_payload = {
                    "name": dataset_name,
                    "description": dataset_description,
                    "source_document": document_name,  # Используем document_name вместо имени файла
                    "questions": [],  # Пустой список вопросов - они будут добавляться по мере обработки
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
                
                # Подготавливаем задачи для добавления в очередь
                tasks = []
                skipped_chunks = 0
                for idx, chunk in enumerate(chunks, 1):
                    # Извлекаем текст из чанка (может быть словарь или строка)
                    if isinstance(chunk, dict):
                        chunk_text = chunk.get("fragment_data", {}).get("combined_text", "")
                        # Если combined_text пустой, пытаемся получить из других полей
                        if not chunk_text:
                            chunk_text = chunk.get("fragment_data", {}).get("content", "")
                        if not chunk_text:
                            chunk_text = chunk.get("fragment_data", {}).get("title", "")
                        # Если все еще пусто, используем строковое представление
                        if not chunk_text:
                            chunk_text = str(chunk)
                    else:
                        chunk_text = str(chunk)
                    
                    # Пропускаем пустые чанки или слишком короткие
                    if not chunk_text or len(chunk_text.strip()) < 10:
                        skipped_chunks += 1
                        st.warning(f"⚠️ Chunk {idx} пропущен: пустой или слишком короткий текст")
                        continue
                    
                    for question_type in question_types:
                        task_data = {
                            "chunk_id": idx,
                            "chunk_text": chunk_text,
                            "question_type": question_type,
                            "source_document": document_name,  # Используем document_name вместо имени файла
                            "dataset_name": dataset_name,
                            "dataset_id": dataset_result['dataset_id'],  # Добавляем ID датасета
                            "dataset_description": dataset_description,
                            "priority": 1
                        }
                        tasks.append(task_data)
                
                if skipped_chunks > 0:
                    st.info(f"ℹ️ Пропущено {skipped_chunks} пустых чанков из {len(chunks)}")
                
                # Добавляем задачи в очередь
                tasks_response = post(f"/queues/{queue_name}/tasks/", json=tasks)
                
                if tasks_response.status_code == 200:
                    result = tasks_response.json()
                    st.success(f"✅ Successfully added {result['tasks_added']} tasks to queue '{queue_name}'")
                    st.info(f"📋 Queue: {queue_name}")
                    st.info(f"📊 Dataset: {dataset_name} (ID: {dataset_result['dataset_id']})")
                    st.info(f"🔧 Tasks will be processed by the worker in the background")
                    st.info(f"📊 You can monitor progress in the Queue Manager page")
                    
                    # Показываем ссылку на мониторинг
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
            
            # Создаем прогресс-бар
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            if not API_GEN_QUE_URL:
                st.error("⛔ API_GEN_QUE_URL is not configured. Please check your environment variables.")
                st.stop()
                
            generate_url = API_GEN_QUE_URL

            question_counter = 0
            total_questions = len(chunks) * len(question_types)
            
            for idx, chunk in enumerate(chunks, 1):
                # Извлекаем текст из чанка (может быть словарь или строка)
                if isinstance(chunk, dict):
                    chunk_text = chunk.get("fragment_data", {}).get("combined_text", str(chunk))
                else:
                    chunk_text = str(chunk)
                
                # Генерируем вопросы для каждого типа
                for question_type in question_types:
                    question_counter += 1
                    
                    # Обновляем прогресс
                    progress = question_counter / total_questions
                    progress_bar.progress(progress)
                    status_text.text(f"Processing chunk {idx}/{total_chunks}, question type: {question_type} ({question_counter}/{total_questions})...")

                    payload = {
                        "prompt": chunk_text,
                        "question_type": question_type,
                        "source": "user_input",
                        "chat_id": 12345,
                        "source_text": chunk_text
                    }

                    try:
                        res = requests.post(generate_url, json=payload)
                        if res.status_code == 200:
                            output = res.json().get("result", {}).get("output", {})
                            gq = output.get("generated_question", {})
                            sensitivity = output.get("sensitivity_score", {})
                            validation = output.get("validation_result", {})

                            # Все непустые варианты ответа option_*
                            options = [
                                (int(k.replace("option_", "")), v)
                                for k, v in gq.items()
                                if k.startswith("option_") and v not in [None, "None"]
                            ]
                            options.sort()
                            options_text = "\n".join(f"{i}. {text}" for i, text in options) if options else "No options provided"
                            
                            # Сохраняем options как словарь для сохранения в БД
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
                                "Options Dict": options_dict,  # Сохраняем словарь для БД
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

            # Завершаем прогресс-бар после обработки всех чанков
            progress_bar.progress(1.0)
            status_text.text("✅ Generation completed!")
            
            # Отображаем результаты только после завершения всех обработок
            df = pd.DataFrame(results)
            st.success("✅ Generation completed.")
        
        # Отображаем результаты только если они есть (для Direct Processing)
        if results:
            # Группируем результаты по чанкам для лучшего отображения
            chunks_with_questions = {}
            for item in results:
                chunk_id = item['Chunk #']
                if chunk_id not in chunks_with_questions:
                    chunks_with_questions[chunk_id] = []
                chunks_with_questions[chunk_id].append(item)
            
            # Отображаем результаты по чанкам
            for chunk_id, questions in chunks_with_questions.items():
                st.markdown("---")
                st.markdown(f"### 🧩 Chunk #{chunk_id}")
            
                # Показываем исходный текст только один раз для чанка
                if questions and questions[0].get('Source Chunk'):
                    with st.expander("📄 View Source Text", expanded=False):
                        st.markdown("**Source Text:**")
                        st.markdown(f"```\n{questions[0]['Source Chunk']}\n```")
                
                # Отображаем все вопросы для этого чанка
                for i, item in enumerate(questions, 1):
                    st.markdown(f"#### Question {i} ({item['Question Type']})")
                    st.markdown(f"**Task:** {item['Task']}")
                    st.markdown("**Options:**")
                    st.markdown(item['Options'])
                    st.markdown(f"**Correct Answer:** {item['Correct Answer']}")
                    
                    # Отображение провокативности
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown(f"**Provocativeness Score:** {item['Provocativeness']}")
                    
                    # Отображение валидации
                    with col2:
                        validation_score = item['Validation Score']
                        validation_threshold = item.get('Validation Threshold', 'N/A')
                        validation_passed = item['Validation Passed']
                        
                        if validation_passed:
                            st.markdown(f"**Validation:** ✅ {validation_score} (Threshold: {validation_threshold}) PASSED")
                        else:
                            st.markdown(f"**Validation:** ❌ {validation_score} (Threshold: {validation_threshold}) FAILED")
                    
                    # Детальная информация о валидации
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

        # Скачивание CSV только после завершения всех обработок
        
        # Сохранение датасета в MongoDB только если есть результаты (для Direct Processing)
        if results:
            st.markdown("---")
            st.markdown("### 💾 Save Dataset to Database")
            
            try:
                # Подготавливаем данные для сохранения
                questions_data = []
                for item in results:
                    questions_data.append({
                        "chunk_id": item['Chunk #'],
                        "source_chunk": item['Source Chunk'],
                        "question_type": item['Question Type'],
                        "task": item['Task'],
                        "options": item.get('Options Dict', item['Options']),  # Используем словарь если есть
                        "correct_answer": str(item['Correct Answer']),
                        "provocativeness": str(item['Provocativeness']),
                        "validation_score": str(item['Validation Score']),
                        "validation_threshold": str(item.get('Validation Threshold', 'N/A')),
                        "validation_passed": str(item['Validation Passed']),
                        "validation_details": str(item['Validation Details'])
                    })
                
                # Создаем датасет
                dataset_payload = {
                    "name": dataset_name,
                    "description": dataset_description,
                    "source_document": document_name,  # Используем document_name вместо имени файла
                    "questions": questions_data,
                    "metadata": {
                        "question_types": question_types,
                        "num_chunks_processed": len(chunks),
                        "total_chunks": len(chunks),
                        "total_questions_generated": len(results),
                        "questions_per_chunk": len(question_types),
                        "generated_at": datetime.now().isoformat()
                    }
                }
                
                # Отправляем запрос на сохранение
                st.write("Debug: Dataset payload:", dataset_payload)
                save_response = post("/datasets/", json=dataset_payload)
                
                if save_response.status_code == 200:
                    save_result = save_response.json()
                    st.success(f"✅ Dataset saved successfully! Dataset ID: {save_result['dataset_id']}")
                    st.info(f"📊 Dataset '{dataset_name}' (version {save_result['version']}) has been saved to the database.")
                else:
                    st.error(f"❌ Error saving dataset: {save_response.status_code}")
                    st.error(f"Response text: {save_response.text}")
                    st.json(save_response.text)
                    
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Download as CSV", data=csv, file_name="generated_tasks.csv", mime="text/csv", key="results_csv_download")
                    
            except requests.exceptions.ConnectionError:
                st.error("⛔ Could not connect to the Dataset API server. Make sure it is running.")
            except Exception as e:
                st.error(f"❌ Error saving dataset: {str(e)}")
