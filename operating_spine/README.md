# Aldernia Operating Spine — Phase 1

Status: PRODUCTION-INTEGRATED INTERNAL CONTROL PACKAGE / NON-AUTHORITATIVE PROJECTION / NOT A SOURCE OF TRUTH

This package implements the bounded Phase 1 machine contracts for the Aldernia Operating Spine in the production repository. It does not create a new institution, alter public editorial content, activate payments, collect user data, or confer authority.

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
- `projection/manifest.json` — snapshot metadata and verified public-release identity used as the Phase 1 baseline.
- `validate.py` — dependency-free deterministic validation and cross-reference checks.
- `tests/test_operating_spine.py` — unit tests for schema/projection invariants.

## Public-release baseline used by Phase 1

Phase 1 was grounded against the canonical Public World Blueprint and independently verified technical evidence:

- baseline production main: `81f8af9942881738977b0913eed2f96d1c0c78d2`;
- public build identity: `ALD-CROWN-FOUNDING-09`;
- official GitHub Pages run: `35560455677` — success;
- served public smoke: `35560456216` — observed `ALD-CROWN-FOUNDING-09`;
- served browser/accessibility run: `35560456252` — observed `ALD-CROWN-FOUNDING-09` and passed all configured routes/viewports.

The Public World Blueprint contains that public release reconciliation. Production integration of the Operating Spine changes repository/control code only; it does not by itself create a new public editorial build identity.

## Validation

Run:

```bash
python -m unittest tests.test_operating_spine
python operating_spine/validate.py
```

The validator checks structural requirements, allowed enums, source/owner cross-references, Drive-only canonical pointers, projection non-authority, exact baseline release identity, and the House of Carol support-only boundary.

## Phase 1 limitation

This is a static, manually compiled snapshot proving the contract. It is production-integrated but deliberately not connected to automated Drive writes or used as a second canonical database. The public-site runtime does not import it. A later authorised increment may add a read-only compiler from current canonical sources while preserving the same authority boundary.
