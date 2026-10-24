"""FlashTrace engine: pure-NumPy WCAG 2.2 SC 2.3.1 analysis of a frame sequence.

Analysis aid, not a compliance certification.  A PASS means "no 2.3.1
violation detected under the tested conditions".  See docs/SPEC.md section 5
and docs/ENGINE.md for the pipeline.
"""

from engine.analyze import analyze, evidence_frame_indices
from engine.constants import ENGINE_VERSION
from engine.types import AnalysisResult

__all__ = ["analyze", "evidence_frame_indices", "AnalysisResult", "ENGINE_VERSION"]
