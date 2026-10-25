"""`flashtrace bench` passes its ablation-sample options through to bench.run.run_bench."""
from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from cli.main import app


def test_ablation_sample_options_reach_run_bench(monkeypatch: Any) -> None:
    """ADR-028: --ablate-sample K and --ablate-duration D are forwarded unchanged."""
    seen: dict[str, Any] = {}

    def fake_run_bench(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"summaryLines": ["bench: fake run"]}

    monkeypatch.setattr("bench.run.run_bench", fake_run_bench)
    result = CliRunner().invoke(app, ["bench", "--n", "3", "--ablate-sample", "2", "--ablate-duration", "4"])
    assert result.exit_code == 0, result.output
    assert seen["ablate_sample"] == 2 and seen["ablate_duration"] == 4.0 and seen["n"] == 3
    assert "Analysis aid, not a compliance certification." in result.output


def test_ablation_sample_defaults_to_off(monkeypatch: Any) -> None:
    """Without the options no ablation sample runs and the duration falls back to --duration."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr("bench.run.run_bench", lambda **kw: seen.update(kw) or {"summaryLines": []})
    assert CliRunner().invoke(app, ["bench", "--n", "1"]).exit_code == 0
    assert seen["ablate_sample"] == 0 and seen["ablate_duration"] is None
