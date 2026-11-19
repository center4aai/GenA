"""Backward-compatible re-export — canonical implementation in chunker/main.py."""
import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_CHUNKER_DIR = Path(__file__).resolve().parent / "chunker"
if str(_CHUNKER_DIR) not in sys.path:
    sys.path.insert(0, str(_CHUNKER_DIR))

_CANONICAL = _CHUNKER_DIR / "main.py"
_spec = spec_from_file_location("_chunker_main", _CANONICAL)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load chunker main from {_CANONICAL}")
_mod = module_from_spec(_spec)
_spec.loader.exec_module(_mod)

create_llm_chunks = _mod.create_llm_chunks
process_document_to_chunks = _mod.process_document_to_chunks
extract_outline_from_document = _mod.extract_outline_from_document
_unwrap_outline_tree = _mod._unwrap_outline_tree

__all__ = ["create_llm_chunks", "process_document_to_chunks", "extract_outline_from_document"]

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Process documents into LLM chunks")
    parser.add_argument("inputs", nargs="+", help="Document or JSON outline paths")
    parser.add_argument("-m", "--min-size", type=int, default=50)
    parser.add_argument("-o", "--output-dir", default=".")
    parser.add_argument("--save-intermediate", action="store_true")
    parser.add_argument("--json-mode", action="store_true")
    args = parser.parse_args()

    if args.min_size < 1:
        print("[ERROR] --min-size must be >= 1", file=sys.stderr)
        sys.exit(2)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    failed = False
    for input_path in args.inputs:
        input_file = Path(input_path).resolve()
        if not input_file.exists():
            print(f"[ERROR] File not found: {input_file}", file=sys.stderr)
            failed = True
            continue
        try:
            if args.json_mode or input_file.suffix.lower() == ".json":
                chunks = create_llm_chunks(str(input_file), min_size=args.min_size)
            else:
                if args.save_intermediate:
                    tree = _unwrap_outline_tree(extract_outline_from_document(str(input_file)))
                    outline_out = output_dir / f"{input_file.stem}_outline.json"
                    outline_out.write_text(
                        json.dumps(tree, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print(f"Wrote outline to {outline_out}")
                chunks, _ = process_document_to_chunks(str(input_file), min_size=args.min_size)
            if not chunks:
                print(f"[ERROR] No chunks produced for {input_file}", file=sys.stderr)
                failed = True
                continue
            out = output_dir / f"llm_chunks_{input_file.stem}.json"
            out.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Wrote {len(chunks)} chunks to {out}")
        except Exception as exc:
            print(f"[ERROR] {input_file}: {exc}", file=sys.stderr)
            failed = True

    sys.exit(1 if failed else 0)
