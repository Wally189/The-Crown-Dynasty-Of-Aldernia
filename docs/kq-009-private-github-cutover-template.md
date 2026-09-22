# KQ-009 — Private GitHub Runtime Cutover Template 01

Status: REVIEW CANDIDATE ONLY — NOT CUT OVER  
Authority basis: Royal Programme Board E-30 / KQ-009 Crown direction of 22 September 2026: stop Google provisioning; prepare a bounded private-GitHub replacement; keep current records authoritative until explicit canonical-home cutover.

## Boundary

This template contains no private Dynasty record content. It defines the smallest record set, repository contract, adapter shape, runtime-state transfer, and acceptance gates needed to review a private-GitHub replacement.

The existing public website repository remains public and is not an acceptable canonical home for private operational records. Existing unrelated private repositories are also not to be reused merely because they are private. The target must be a dedicated private runtime repository.

## One authoritative home rule

Before cutover, every listed record remains authoritative in its current home. The template repository is REVIEW_ONLY and must fail closed as an operational source.

At cutover, each transferred record receives exactly one authoritative private-GitHub path. Its former Drive/public-GitHub location becomes FROZEN_PROVENANCE or DERIVED_READ_ONLY. No live dual-master period is permitted.

Static constitutions and specialist records that the runtime does not need to mutate are not transferred wholesale. They remain in Drive under their existing owners.

## Exact operational transfer set

1. Royal Programme Board — machine-authorised operational rows only. Current home: Scheduled Tasks Register / tab Royal Programme Board (Drive file 1Qk6l3Iy8nAmArQmFfdNUccCB_HyTTp5fWozq_zWVhPA). Proposed home: records/royal-programme-board.json.
2. King's Consideration Queue — current OPEN/APPROVED/BLOCKED runtime-relevant decisions, including KQ-009; not closed historical narrative. Same Drive file / tab King's Consideration Queue. Proposed home: records/kings-consideration-queue.json.
3. Scheduled Tasks — ACTIVE machine-executable/timed duties only; history remains provenance. Same Drive file / tab Scheduled Tasks. Proposed home: records/scheduled-tasks.json.
4. Dynasty Engine Manifest — machine layer definitions required by routing/execution. Current Drive record 1NumS4_cKvgJLQfUIG45Nug7clbv2zuxU2EZ9bvSJdXQ. Proposed home: records/engine-manifest.json.
5. Institutional Capability Registry — machine-routable capability entries needed by COS-SEL/HOS-CAP, without importing unrelated House-private content. Current Drive record 1f9ehuX67WRwYMLK0JD7s1q-NEN9BGqdzNC3kfxyLl7s. Proposed home: records/capability-registry.json.
6. Runtime state currently on public branch clock-state: state/clock-state.json; state/bus-session.json; state/bus-state.json; state/scheduled-duty-queue.json; state/patrol-budget.json; state/runtime-health.json; state/opportunity-resolver-state.json; state/opportunity-resolver-trace.json; state/opportunity-stage3-state.json; state/opportunity-stage3-trace.json; state/opportunity-stage4-state.json; state/opportunity-stage4-trace.json. Proposed home: private repository branch runtime-state, same state/ paths.
7. The current state/canonical-programme-board.json cache is NOT transferred as a separate authority. It is replaced by direct local reading of records/royal-programme-board.json; a packet may be generated in memory or as derived runtime state.
8. aldernia/schedule/timetable.json is NOT a second canonical schedule. After cutover it must be generated from records/scheduled-tasks.json or removed from the authority path.

## Explicit non-transfer

CAT-123; the full Royal Palace settlement; full House-of-Series assurance register; full specialist House estates; Royal Household material; archived records; website content; public-world records; Gmail/Calendar; secrets; provider credentials; whole Drive folders.

These remain where presently governed unless separately authorised.

## Private repository/access gate

Cutover acceptance requires a dedicated repository whose visibility is PRIVATE, owned by the authorised GitHub account/organisation, and accessible to the runtime workflow. The workflow must use only the repository-scoped ephemeral GITHUB_TOKEN with minimum permissions. No PAT, deploy key, Google identity, long-lived cloud key, public fork, Pages publication, or cross-repository write is required for the storage cutover.

The candidate workflow template fails closed unless repository privacy is true and an operator-set zero-spend attestation is present. The attestation is not a substitute for account billing controls.

## Zero-spend gate

Before enabling a schedule, verify the GitHub account's Actions billing controls in GitHub Billing. Private repositories consume included Actions minutes; usage beyond the included allowance can be billable. The accepted configuration must either have no valid payment method capable of Actions overage, or an Actions budget/spending control that prevents further paid usage when the limit is reached. Included-usage alerts should be enabled.

Repository/workflow controls in this template additionally:
- standard ubuntu runner only;
- timeout-minutes: 5;
- no larger runner;
- no artifact upload;
- no cache;
- no external provider authentication;
- no package/container publication;
- no Pages deployment;
- no model call in the storage acceptance path;
- fail closed when zero-spend attestation is absent.

Account-level billing state cannot be proven by repository code and remains a cutover precondition.

## Revised transport acceptance

Replace LIVE_GOOGLE_DRIVE_API with PRIVATE_GITHUB_REPOSITORY for this storage architecture. Acceptance before cutover:

A. target repository readback proves visibility=private and expected owner/repository identity;
B. manifest validates one authoritative home per transferred record and cutover_state=ACTIVE only after explicit approval;
C. unchanged canonical record content produces the same evidence hash across unrelated commits;
D. exact material record change produces a new evidence hash;
E. E-28 durable ACK remains NO_ACTION on unchanged evidence;
F. private-record loss/malformed content fails closed without freezing unrelated deterministic clock work;
G. public repository contains no transferred private record content;
H. runtime-state writes are confined to the private runtime repository/runtime-state branch;
I. GITHUB_TOKEN permissions are contents:write only for persistence; no id-token permission;
J. no Google token/WIF/service-account path is required;
K. no long-lived GitHub credential is required;
L. zero-spend account gate is verified before schedule activation;
M. credential/log scan finds no secret material;
N. schedule remains best-effort and missed runs are reconciled by durable queue semantics rather than invented execution.

## Review/cutover sequence

REVIEW_TEMPLATE -> PRIVATE_REPO_CREATED -> ACCESS_VERIFIED -> ZERO_SPEND_VERIFIED -> DATA_STAGED_NONAUTHORITATIVE -> HASH_PARITY_VERIFIED -> CROWN_CANONICAL_HOME_APPROVAL -> CUTOVER_ACTIVE -> OLD_HOMES_FROZEN_PROVENANCE -> LIVE_ACCEPTANCE -> CLOSE.

This branch intentionally stops at REVIEW_TEMPLATE.
