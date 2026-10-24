"""Repository-wide rules from the build brief, enforced by tests.

1. FlashTrace never claims a page is "WCAG compliant" (a PASS means "no 2.3.1 violation detected under
   the tested conditions"). Product source and UI text must not contain the phrase.
2. Every report carries the disclaimer, pinned as a constant in the report schema.
3. No AI/LLM client libraries are imported anywhere in the product.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories that make up the product (docs may *mention* the phrase when explaining that we avoid it).
PRODUCT_DIRS = ["engine", "capture", "cli", "server", "bench", "action", "demo", "web/app", "web/components", "web/lib"]
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".html", ".css", ".yml", ".yaml", ".md", ".json"}
SKIP_PARTS = {"node_modules", ".next", "pages", "__pycache__"}  # bench/pages is generated output

FORBIDDEN_PHRASE = re.compile(r"wcag[\s-]*compliant", re.IGNORECASE)
FORBIDDEN_IMPORTS = re.compile(r"^\s*(import|from)\s+(openai|anthropic|google\.generativeai|langchain|transformers|ollama)\b", re.MULTILINE)


def product_files():
    for d in PRODUCT_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in SOURCE_SUFFIXES and not (set(p.parts) & SKIP_PARTS):
                yield p


def test_no_wcag_compliant_claim_in_product():
    """Brief: never output "WCAG compliant"."""
    offenders = [str(p.relative_to(ROOT)) for p in product_files() if FORBIDDEN_PHRASE.search(p.read_text(errors="ignore"))]
    assert offenders == [], f"forbidden phrase 'WCAG compliant' found in: {offenders}"


def test_schema_pins_disclaimer():
    """Brief: every report carries "Analysis aid, not a compliance certification."."""
    schema = json.loads((ROOT / "engine/schema/report.schema.json").read_text())
    assert schema["properties"]["disclaimer"]["const"] == "Analysis aid, not a compliance certification."
    assert "disclaimer" in schema["required"]


def test_no_llm_imports_in_product():
    """Brief: no AI/LLM anywhere in the product."""
    offenders = [str(p.relative_to(ROOT)) for p in product_files() if p.suffix == ".py" and FORBIDDEN_IMPORTS.search(p.read_text(errors="ignore"))]
    assert offenders == [], f"LLM client import found in: {offenders}"
