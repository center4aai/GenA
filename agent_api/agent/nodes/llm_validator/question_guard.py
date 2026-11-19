"""Deterministic checks for forbidden source-text references in question wording.

Used for textbook / encyclopedia document kinds. Normative docs legitimately
cite the source document, so the guard must not run for them.
"""

from __future__ import annotations

import re
from typing import Optional

# Kinds where references like «в тексте» / «описанный в тексте» are forbidden.
GUARDED_DOCUMENT_KINDS = frozenset({"textbook", "encyclopedia"})

# Index of question_context inside c1_question scores (0-based).
QUESTION_CONTEXT_INDEX = 4

# After «тексте/фрагменте/…», a Capitalized word usually names a document
# («в тексте Конституции РФ») and is legitimate for normative docs. Skip those.
# Keep this lookahead case-sensitive even when the rest of the pattern is (?i).
_NOT_DOC_TITLE = r"(?!\s+(?-i:[А-ЯЁA-Z]))"

# «автора» may legitimately be followed by a named work — «по мнению автора
# «Капитала»». Same idea as _NOT_DOC_TITLE, but an opening quote also counts.
_NOT_NAMED_WORK = r"(?!\s*(?:[«\"„]|(?-i:[А-ЯЁA-Z])))"

# Only the noun «автор» itself, declined — not «авторитетный», «авторский».
_AUTHOR = r"автор(?:а|у|ом|е|ы|ов|ам|ами|ах)?(?!\w)"

# Generic deictic nouns: «текст», «описание», «анализ»… Title-lookahead applies
# so «в тексте Конституции РФ» stays legitimate.
_GENERIC_LOC = (
    r"(?:тексте|фрагменте|отрывке|материале|документе|"
    r"описании|изложении|анализе|источнике)"
)
_GENERIC_DAT = (
    r"(?:тексту|фрагменту|отрывку|материалу|документу|"
    r"описанию|изложению|анализу|источнику)"
)

# Structural nouns: any mention is forbidden for textbook/encyclopedia,
# even when followed by a title («в главе 22 учебника»).
_STRUCT_LOC = r"(?:учебнике|главе|параграфе|разделе|книге|пособии|энциклопедии)"
_STRUCT_DAT = r"(?:учебнику|главе|параграфу|разделу|книге|пособию|энциклопедии)"

_ALL_LOC = rf"(?:{_GENERIC_LOC}|{_STRUCT_LOC})"

_QUALIFIER = (
    r"(?:данн\w*|этом|привед[её]нн\w*|указанн\w*|представленн\w*|"
    r"исходн\w*|рассматриваем\w*|вышеуказанн\w*|нижеследующ\w*)"
)
# Single «н» on purpose: the short forms («описаны», «перечислены») are what a
# question actually uses, and they were not covered — «описаны в статье» slipped
# through entirely, since unlike «в тексте» there is no catch-all for «в статье».
_PARTICIPLE = (
    r"(?:описан\w*|упомянут\w*|упомина\w*|указан\w*|перечислен\w*|"
    r"привед[её]н\w*|назван\w*|рассмотрен\w*|содержащ\w*)"
)
_VERBAL = (
    r"(?:описано|указано|сказано|говорится|отмечается|изложено|упоминается)"
)


_SOURCE_REF_PATTERNS = [
    # «в/во <qualifier> тексте/описании/…» (skip «в тексте Конституции…»)
    re.compile(
        r"(?i)"
        r"(?:^|[^\w])"
        r"(?:в|во)\s+"
        + _QUALIFIER
        + r"\s+"
        + _GENERIC_LOC
        + _NOT_DOC_TITLE
    ),
    # «в/во <qualifier> учебнике/главе/…» — no title skip
    re.compile(
        r"(?i)"
        r"(?:^|[^\w])"
        r"(?:в|во)\s+"
        + _QUALIFIER
        + r"\s+"
        + _STRUCT_LOC
    ),
    # «согласно <optional words> тексту/описанию/анализу/…»
    re.compile(
        r"(?i)"
        r"соглас\w+\s+(?:\w+\s+){0,2}"
        + _GENERIC_DAT
        + _NOT_DOC_TITLE
    ),
    # «согласно <optional words> учебнику/главе/…»
    re.compile(
        r"(?i)"
        r"соглас\w+\s+(?:\w+\s+){0,2}"
        + _STRUCT_DAT
    ),
    # participial: «описанный в тексте», «упоминаются в тексте», …
    re.compile(
        r"(?i)"
        + _PARTICIPLE
        + r"\s+(?:в|во)\s+(?:\w+\s+){0,2}"
        + _GENERIC_LOC
        + _NOT_DOC_TITLE
    ),
    re.compile(
        r"(?i)"
        + _PARTICIPLE
        + r"\s+(?:в|во)\s+(?:\w+\s+){0,2}"
        + _STRUCT_LOC
    ),
    # «как описано / говорится в учебнике|тексте|…»
    re.compile(
        r"(?i)"
        r"(?:как\s+)?"
        + _VERBAL
        + r"\s+(?:в|во)\s+(?:\w+\s+){0,2}"
        + _ALL_LOC
        + _NOT_DOC_TITLE
    ),
    # «на основе приведённых/указанных/… фактов»
    re.compile(
        r"(?i)"
        r"на\s+основе\s+"
        r"(?:привед[её]нн\w*|указанн\w*|изложенн\w*|перечисленн\w*|описанн\w*)"
    ),
    # «в описанный / рассматриваемый / указанный период»
    re.compile(
        r"(?i)"
        r"(?:^|[^\w])(?:в|во)\s+"
        r"(?:описанн\w*|рассматриваем\w*|указанн\w*)\s+"
        r"(?:период\w*|врем\w*|эпох\w*|ситуаци\w*)"
    ),
    # «в главе / параграфе / разделе» + номер (word boundary: not «разделении»)
    re.compile(
        r"(?i)"
        r"(?:^|[^\w])(?:в|во)\s+(?:главе|параграфе|разделе)(?!\w)(?:\s+\d+)"
    ),
    # «по тексту» / «по данному тексту»
    re.compile(r"(?i)(?:^|[^\w])по\s+(?:данному\s+)?тексту" + _NOT_DOC_TITLE + r"(?:[^\w]|$)"),
    # bare catch-all «в тексте» / «во тексте» (not «в тексте Конституции…»)
    re.compile(
        r"(?i)(?:^|[^\w])(?:в|во)\s+тексте" + _NOT_DOC_TITLE + r"(?:[^\w]|$)"
    ),
    # encyclopedia / course: «согласно статье БРЭ», «в данном томе», «в курсе»
    re.compile(
        r"(?i)соглас\w+\s+статье\s+(?:брэ|бсе|энциклопед\w*|большой\s+российской)"
    ),
    # «описаны в статье», «перечислены в статье Большой российской энциклопедии».
    # A NUMBERED article is a legal citation about the world («в статье 15
    # Конституции РФ») and stays allowed; an unnumbered one can only mean the
    # encyclopedia entry the chunk came from.
    re.compile(
        r"(?i)"
        + _PARTICIPLE
        + r"\s+(?:в|во)\s+(?:\w+\s+){0,2}стать[еия](?!\s*\d)"
    ),
    re.compile(
        r"(?i)(?:как\s+)?"
        + _VERBAL
        + r"\s+(?:в|во)\s+(?:\w+\s+){0,2}стать[еия](?!\s*\d)"
    ),
    re.compile(
        r"(?i)(?:^|[^\w])(?:в|во)\s+(?:данном|этом|указанном)\s+томе"
    ),
    # Volume, not «томографический»/«томление» — keep the ending closed.
    re.compile(r"(?i)данн\w*\s+том(?:а|е|ом|ов|ам|ами|ах|у)?(?!\w)"),
    # «в курсе» means the course of study — a reference to the source. It is not
    # one in the exchange-rate sense («изменения в курсе рубля») or the
    # being-informed sense («кто был в курсе заговора»), so those are excluded.
    re.compile(
        r"(?i)(?:^|[^\w])(?:в|во)\s+(?:данном\s+|этом\s+|указанном\s+|нашем\s+)?курсе(?!\w)"
        r"(?!\s+(?:рубл|доллар|евро|валют|акци|бумаг|обмен|дел(?!ени)|событ|заговор|"
        r"перегово|происход|подготов|расследован))"
    ),
    re.compile(r"(?i)из\s+текста\s+следует"),
    re.compile(r"(?i)вышепривед[её]нн\w+"),
    # «по мнению автора», «по словам автора» — but not «по мнению автора „Капитала"»
    re.compile(
        r"(?i)по\s+(?:мнению|словам|оценке|версии)\s+" + _AUTHOR + _NOT_NAMED_WORK
    ),
    # «автор считает / полагает / утверждает / отмечает / пишет»
    re.compile(
        r"(?i)(?:^|[^\w])(?:как\s+)?" + _AUTHOR + r"\s+"
        r"(?:счита\w+|полага\w+|утвержда\w+|отмеча\w+|подчёркива\w+|"
        r"подчеркива\w+|указыва\w+|пишет|пишут)"
    ),
    # participle immediately followed by «как»: «указаны как», «названы как».
    # «указаны в Конституции РФ как…» does not match — «как» must be adjacent.
    re.compile(
        r"(?i)(?:^|[^\w])"
        r"(?:указан|назван|перечислен|упомянут|отмечен|привед[её]н|обозначен)\w*"
        r"\s+как(?!\w)"
    ),
    # «согласно изложенным фактам», «согласно приведённым данным»
    re.compile(
        r"(?i)соглас\w+\s+"
        r"(?:изложенн|привед[её]нн|указанн|описанн|перечисленн|"
        r"представленн|рассмотренн)\w*(?:[^\w]|$)"
    ),
]


def find_source_reference(task: str) -> Optional[str]:
    """Return the matched forbidden phrase, or None if the task is clean."""
    if not task or not isinstance(task, str):
        return None
    text = task.strip()
    if not text:
        return None
    for pattern in _SOURCE_REF_PATTERNS:
        m = pattern.search(text)
        if m:
            matched = m.group(0).strip(" .,;:!?«»\"'()[]")
            return matched or m.group(0).strip()
    return None


def find_source_reference_in_question(question: dict) -> Optional[str]:
    """Scan the question wording for a forbidden source-text reference.

    Only ``task`` is checked. The verdict zeroes ``question_context`` — a
    criterion about how the *question* is phrased — so reading the answer or the
    options was wrong twice over: for open questions ``outputs`` is the
    reference answer, where «в описанный период» is perfectly legitimate, and a
    match there failed the question with a justification pointing at wording it
    does not have.
    """
    if not isinstance(question, dict):
        return find_source_reference(str(question or ""))
    return find_source_reference(str(question.get("task") or ""))


def should_apply_source_ref_guard(document_kind: Optional[str]) -> bool:
    kind = (document_kind or "").strip().lower()
    return kind in GUARDED_DOCUMENT_KINDS
