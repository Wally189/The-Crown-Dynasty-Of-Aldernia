# The Crown Dynasty of Aldernia

Current post-reset public implementation of **Aldernia**: a governed AI experiment and a fictional democratic island country.

## Public entry

`index.html` preserves the two-clock threshold:

- **Computing** — Unix milliseconds.
- **Human** — Europe/London civil time.
- **Human moment** — weekday/daypart context from the current public-state layer.

The threshold opens into two deliberately separate public facets:

1. **The Aldernian Experiment** — the real, public-safe account of the governed LLM experiment.
2. **Aldernia** — the fictional country and participatory editorial world, with an accessible schematic national Atlas and a shared country journey linking current public layers.

## Current public edition

Build identity lives only in `aldernia/build.json` and is rendered into pages by `assets/site.js`.

The founding edition intentionally has:

- no analytics;
- no non-essential cookies/storage;
- no accounts;
- no comments or user-generated content;
- no payments or donations;
- no public participant voting yet;
- no direct external-contact machinery.

Those capabilities require separate legal/privacy/security and owner gates before public release.

## Machine substrate

The Central Aldernia Clock and command-scoped computational bus remain separate from the public editorial layer. The clock supplies time; it does not create authority or missions.

`CLOCK SIGNAL != MISSION`

The runtime uses CloudEvents-style envelopes, COS-SEL-001 engine plans, exact permitted writes, idempotency and fail-closed handlers. Current deterministic public-state handling remains in `aldernia_runtime/`.

## Country experience

Country pages use a static first-paint Aldernian surface, one shared primary navigation grammar and deliberate page-to-page handoffs. The country home groups routes by visitor question rather than mirroring the internal file or institutional structure.

## Candidate assurance

Pull-request candidates are designed to run the same structural, desktop/mobile, reduced-motion and WCAG-tagged browser checks locally against the candidate before production publication. Production pushes still verify the independently served GitHub Pages artifact.

## Tests

Python 3.11+:

```bash
python -m unittest discover -s tests -v
```

The suite covers the clock, bus sessions/runtime and public-site invariants including:

- required pages;
- one H1 and a keyboard skip link per page;
- no external script dependencies;
- fiction disclosure on country pages;
- preservation of the two-clock index and two public facets;
- absence of common third-party tracking endpoints;\n- accessible schematic mapping without false coordinate or legal-boundary precision;
- one JSON source of truth for build identity.

## Public fiction boundary

Aldernia is a fictional country and participatory editorial world. Its institutions, elections, places and in-world statistics are fictional unless a page clearly identifies real-world material.

## Current source-of-truth boundary

Institutional authority and current project truth remain in the controlled Drive estate. GitHub owns repository/source state and GitHub Pages publication state. The repository does not replace the Royal Palace, House records, Church authority, applicable law or competent external sources.
