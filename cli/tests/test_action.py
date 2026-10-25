"""Checks that action/action.yml (the GitHub Action) matches the command line it drives.

A tiny line-based reader, not a YAML parser (no new dependency): the Action's "Run the audit" step must
only use flags that ``flashtrace audit --help`` lists, and every input the Action declares must be used.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ACTION = Path(__file__).resolve().parents[2] / "action" / "action.yml"
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z-]*")


def action_lines() -> list[str]:
    return ACTION.read_text(encoding="utf-8").splitlines()


def declared_inputs(lines: list[str]) -> dict[str, list[str]]:
    """Input name -> its own lines, for every 2-space-indented key under the top-level ``inputs:``."""
    inputs: dict[str, list[str]] = {}
    inside, current = False, None
    for line in lines:
        if not line.startswith(" ") and line.strip():
            inside = line.strip() == "inputs:"
            continue
        if inside and re.fullmatch(r"  [a-z][a-z0-9-]*:\s*", line):
            current = line.strip()[:-1]
            inputs[current] = []
        elif inside and current is not None:
            inputs[current].append(line)
    return inputs


def audit_step_flags(lines: list[str]) -> set[str]:
    """Flags written in the "Run the audit" step (the flashtrace audit call and the NO_FAIL switch)."""
    start = next(i for i, line in enumerate(lines) if line.strip() == "- name: Run the audit")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("- name:"))
    step = lines[start:end]
    assert any("flashtrace audit" in line for line in step), "the Run the audit step must call flashtrace audit"
    return {flag for line in step for flag in FLAG.findall(line)}


def audit_help() -> str:
    environment = dict(os.environ, COLUMNS="200", TERMINAL_WIDTH="200", NO_COLOR="1")
    result = subprocess.run([sys.executable, "-m", "cli.main", "audit", "--help"], capture_output=True, text=True,
                            timeout=120, env=environment, cwd=ACTION.parent.parent)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_every_audit_flag_in_the_action_exists() -> None:
    flags = audit_step_flags(action_lines())
    assert {"--duration", "--viewport", "--fps", "--attribute", "--out", "--html", "--no-fail"} <= flags
    help_text = audit_help()
    missing = sorted(flag for flag in flags if not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", help_text))
    assert missing == [], f"action.yml uses flags that flashtrace audit does not have: {missing}"


def test_every_declared_input_is_used() -> None:
    lines = action_lines()
    inputs = declared_inputs(lines)
    assert {"url", "duration", "viewport", "fps", "attribute", "fail-on-inconclusive", "report-json",
            "report-html", "python-version"} <= set(inputs)
    text = "\n".join(lines)
    unused = sorted(name for name in inputs if f"inputs.{name} " not in text and f"inputs.{name}}}" not in text)
    assert unused == [], f"inputs declared but never used: {unused}"


def test_attribute_input_defaults_to_bbox() -> None:
    attribute = declared_inputs(action_lines())["attribute"]
    assert any(line.strip() == 'default: "bbox"' for line in attribute)
