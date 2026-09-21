# Aldernia Operating Spine — Phase 1

Status: NON-PRODUCTION / INTERNAL PROJECTION / NOT A SOURCE OF TRUTH

This package implements the bounded Phase 1 machine contracts for the Aldernia Operating Spine. It does not create a new institution, publish public content, change the current website, activate payments, collect user data, or confer authority.

## Authority boundary

Controlled Google Drive records remain canonical. GitHub remains source/release/deployment evidence. Chat/model memory remains working context only.

The projection therefore has one hard invariant:

> A compiled projection may describe accepted canonical state; it may never silently create or overrule canonical state.

Every projected state object and event is marked `projection_authoritative: false`.

## Files

- `schemas/aldernia-state-object.schema.json` — common state-object contract.
- `schemas/aldernia-event.schema.json` — append-only event contract.
- `schemas/source-record.schema.json` — provenance/source contract.
- `projection/canonical-owners.json` — current owner/discovery map and explicit non-owners.
- `projection/state-objects.json` — bounded snapshot of current spine-relevant state.
- `projection/events.json` — bounded accepted-event snapshot.
- `projection/source-records.json` — provenance for this snapshot.
- `projection/manifest.json` — snapshot metadata and verified release identity.
- `validate.py` — dependency-free deterministic validation and cross-reference checks.
- `tests/test_operating_spine.py` — unit tests for schema/projection invariants.

## Current release evidence

Phase 1 was grounded against the canonical Public World Blueprint and independently verified technical evidence:

- production main: `81f8af9942881738977b0913eed2f96d1c0c78d2`;
- build identity: `ALD-CROWN-FOUNDING-09`;
- official GitHub Pages run: `35560455677` — success;
- served public smoke: `35560456216` — observed `ALD-CROWN-FOUNDING-09`;
- served browser/accessibility run: `35560456252` — observed `ALD-CROWN-FOUNDING-09` and passed all configured routes/viewports.

The current Public World Blueprint already contains this exact release reconciliation. Phase 1 therefore does not duplicate that record.

## Validation

Run:

```bash
python -m unittest tests.test_operating_spine
python operating_spine/validate.py
```

The validator checks structural requirements, allowed enums, source/owner cross-references, Drive-only canonical pointers, projection non-authority, exact release identity, and the House of Carol support-only boundary.

## Phase 1 limitation

This is a static, manually compiled snapshot used to prove the contract. It is deliberately not wired into the public build or to automated Drive writes. A later authorised increment may design a read-only compiler from current canonical sources, but only after the Phase 1 contract and tests are accepted.
