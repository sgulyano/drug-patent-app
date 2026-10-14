"""Leakage guard: the pipeline must never see the held-out answer key."""
import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
KEY = ROOT / "eval" / "golden" / "expected.yaml"
PIPELINE_DIRS = ["etl", "backend", "seed", "frontend/src"]
SKIP = {"node_modules", "__pycache__", "dist"}


def _files():
    for d in PIPELINE_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and not (SKIP & set(p.parts)) and p.suffix in {".py", ".yaml", ".yml", ".ts", ".tsx", ".json", ".txt", ".md"}:
                yield p


def _answer_literals() -> set[str]:
    key = yaml.safe_load(KEY.read_text(encoding="utf-8"))
    out = set()
    for prod in key["products"]:
        for e in prod["patents"]:
            out.add(str(e["appno"]))
            if e.get("patent_no"):
                out.add(str(e["patent_no"]))
    return out


def test_no_pipeline_module_imports_eval():
    for p in _files():
        if p.suffix != ".py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] == "eval" for a in node.names), f"{p} imports eval"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != "eval", f"{p} imports from eval"


def test_no_pipeline_code_opens_the_answer_key():
    for p in _files():
        text = p.read_text(encoding="utf-8")
        assert "expected.yaml" not in text and "golden" not in text, f"{p} references the answer key"


def test_no_answer_numbers_in_pipeline_code():
    literals = _answer_literals()
    patterns = [re.compile(rf"(?<!\d){re.escape(x)}(?!\d)") for x in literals]
    for p in _files():
        text = p.read_text(encoding="utf-8")
        hits = [pat.pattern for pat in patterns if pat.search(text)]
        assert not hits, f"{p} contains answer-key numbers: {hits[:5]}"


def test_seed_contains_only_product_names():
    seed = yaml.safe_load((ROOT / "seed" / "drugs.yaml").read_text(encoding="utf-8"))
    for item in seed["products"]:
        assert set(item) <= {"name", "dosage_form"}, f"seed entry has extra fields: {item}"
        assert not re.search(r"[฀-๿]", item["name"]), "seed must not contain Thai names"
