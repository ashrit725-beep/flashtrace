# FlashTrace

Local-first auditor for WCAG 2.2 Success Criterion 2.3.1 (Three Flashes or Below Threshold). Captures a web
page as deterministic still frames, applies the W3C thresholds, attributes failing flashes to the DOM
element or animation that causes them, proposes a CSS fix and verifies it by re-running the audit.

**Analysis aid, not a compliance certification.** A PASS means "no 2.3.1 violation detected under the tested
conditions".

Status: under construction. The governing specification is [docs/SPEC.md](docs/SPEC.md); decisions are in
[docs/DECISIONS.md](docs/DECISIONS.md); progress in [docs/STATUS.md](docs/STATUS.md); AI assistance is
disclosed in [AI_USAGE.md](AI_USAGE.md). The full README (install, usage, pipeline diagram, limitations) is
written at milestone M9.
