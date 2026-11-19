"""Keep the CLI-style scripts in this directory out of test collection.

`test_api.py`, `test_chunker.py` and `test_question_generation.py` are manual
tools: they take file paths as arguments and expect running services, so pytest
reports them as fixture errors. The real unit tests around them were never
collected at all — `pytest.ini` pointed only at `tests/`.
"""

collect_ignore = [
    "test_api.py",
    "test_chunker.py",
    "test_question_generation.py",
]

import sys
from pathlib import Path

# These tests import `docx2json_outline` expecting the copy in this directory,
# not the thin shim at the repository root.
_CHUNKER_DIR = str(Path(__file__).resolve().parent)
if _CHUNKER_DIR not in sys.path:
    sys.path.insert(0, _CHUNKER_DIR)
