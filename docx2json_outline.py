"""Backward-compatible re-export — canonical implementation in chunker/docx2json_outline.py."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_CANONICAL = Path(__file__).resolve().parent / "chunker" / "docx2json_outline.py"
_spec = spec_from_file_location("_chunker_docx2json_outline", _CANONICAL)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load docx2json_outline from {_CANONICAL}")
_mod = module_from_spec(_spec)
_spec.loader.exec_module(_mod)

__all__ = list(getattr(_mod, "__all__", [n for n in dir(_mod) if not n.startswith("_")]))
for _name in __all__:
    globals()[_name] = getattr(_mod, _name)
