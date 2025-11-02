import random
import copy
from datetime import datetime

from gena.http import _headers
from gena.config import AGENT_API_URL

import requests

import json

def shuffle_questions(questions):
    if not isinstance(questions, list):
        raise TypeError("Ожидается список вопросов")

    questions_copy = copy.deepcopy(questions)

    for question in questions_copy:
        if not isinstance(question, dict):
            continue

        options = question.get('options')
        if not isinstance(options, dict) or len(options) == 0:
            continue

        correct_answer_key = question.get('correct_answer')
        if not correct_answer_key:
            continue  # Нет правильного ответа — пропускаем обновление

        # Определяем, является ли ответ мультиселектом (содержит запятые)
        try:
            # Пытаемся обработать как один номер
            indices = [int(correct_answer_key)]
        except ValueError:
            # Если не получилось — пробуем как список через запятую
            try:
                indices = [int(x.strip()) for x in correct_answer_key.split(',')]
            except (ValueError, AttributeError):
                # Если всё равно не получилось — пропускаем обновление
                continue

        # Получаем оригинальные значения правильных вариантов
        original_correct_values = set()
        for idx in indices:
            option_key = f"option_{idx}"
            if option_key in options:
                original_correct_values.add(options[option_key])

        if not original_correct_values:
            continue  # Не нашли ни одного правильного варианта — пропускаем

        # Перемешиваем значения
        values = list(options.values())
        random.shuffle(values)

        # Создаём новые пары ключ-значение, сохраняя порядок ключей
        new_options = {}
        keys = list(options.keys())  # ['option_1', 'option_2', ...]
        for key, value in zip(keys, values):
            new_options[key] = value

        question['options'] = new_options

        # Находим новые номера для правильных значений
        new_indices = []
        for key, value in new_options.items():
            if value in original_correct_values:
                try:
                    num = int(key.split('_')[1])
                    new_indices.append(num)
                except (IndexError, ValueError):
                    continue

        # Сортируем
        new_indices.sort()

        # Формируем строку ответа: для multi — через запятую, для one — просто число
        if len(new_indices) == 1:
            question['correct_answer'] = str(new_indices[0])
        else:
            question['correct_answer'] = ','.join(map(str, new_indices))

    return questions_copy


def rephrase_questions(dataset_name, questions):
    
    payload = {
        'dataset_name': dataset_name,
        'questions': questions
    }

    agent_api_endpoint = AGENT_API_URL+'/rephrase_questions/'

    tasks_response = requests.post(agent_api_endpoint, headers=_headers(), json = payload).json()

    return tasks_response
