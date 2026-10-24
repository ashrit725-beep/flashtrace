# FlashTrace web UI

The local report viewer for FlashTrace: start an audit, watch it run, read the report
(verdict, flashes-per-second chart, still-frame evidence, heatmap, culprits, CSS fixes)
and browse past audits. It is a static Next.js export with no AI and no network
resources; `flashtrace serve` serves `web/out` and the API from one origin.

- `npm run dev` - development server; point it at a running API with
  `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8001 npm run dev` (default is same origin).
- `npm run build` - static export to `web/out`.
- Safety: nothing here flashes faster than 3 Hz; evidence frames are stills stepped by hand.
- `npm run test:a11y` - builds, then runs the Playwright + axe-core tests against a
  mocked API (`tests/`); `SHOTS=1 npx playwright test screens` also saves screenshots.

Analysis aid, not a compliance certification.
