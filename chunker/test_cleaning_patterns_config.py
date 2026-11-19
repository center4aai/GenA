"""Guard for возможные_паттерны_для_очистки.json.

Despite the scratch-note filename, this JSON is runtime configuration:
load_cleaning_patterns() in docx2json_outline.py reads it next to the script and
apply_cleaning_patterns() runs its regexes over every processed document (ГАРАНТ
and КонсультантПлюс boilerplate removal). Deleting it silently turns document
cleaning into a no-op — it was once removed as "dead" exactly that way. This
test makes that mistake fail CI instead.
"""

import json
import re
from pathlib import Path

_PATTERN_FILE = Path(__file__).parent / "возможные_паттерны_для_очистки.json"


def test_cleaning_patterns_file_exists_and_loads():
    assert _PATTERN_FILE.exists(), (
        f"{_PATTERN_FILE.name} is runtime config for load_cleaning_patterns(); "
        "without it every document is chunked with legal-boilerplate cleaning off."
    )
    data = json.loads(_PATTERN_FILE.read_text(encoding="utf-8"))
    patterns = data.get("patterns", [])
    assert patterns, "the patterns list must not be empty"


def test_every_cleaning_pattern_compiles():
    data = json.loads(_PATTERN_FILE.read_text(encoding="utf-8"))
    for p in data.get("patterns", []):
        # A broken regex is skipped with a warning at runtime, i.e. silently
        # stops cleaning — catch it here instead.
        assert p.get("regex"), f"pattern {p.get('name', '?')} has no regex"
        re.compile(p["regex"])
