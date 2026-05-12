# ADR 0003: Shared Workshop State as Artifact Substrate

## Status

Proposed. Targets v0.2. Promotion to Accepted is gated on a Phase 0
measurement spike (see `scripts/spike_workshop_arms.sh`) and a final
24h acceptance soak per the v0.2 plan.

## Context

ADR 0002 closed the "Layer-patch" pattern. Seven message-level
coercion patches (PRs #17-23) each became the next attractor's
substrate, and the Path-3 stateless tick (PR #24) removed the
autobiographical substrate that fed those attractors. v0.1.1 ships
a structurally correct harness: a 24h, 897-sample structural leak
sweep returned zero self-history substring matches across every
audited context channel.

The corpus that harness actually produces is not yet what the project
was designed to deliver. A representative 24h soak:

- 7,616 accepted artifacts; average 17.99 words; max 50.
- 83% of artifacts are 15-39 words — single-sentence object
  descriptions ("small wooden box carved with cherry blossoms",
  "whistle painted with spring motifs").
- Aki (Artisan) holds 93-94% craft share every hour of every 24h
  run. Themes cluster on cherry blossoms, wooden vessels, dew drops.
- Cy (Scholar) study 59-71% with shorter field-note artifacts.

A thinking-frameworks analysis named three reinforcing root causes:

1. **Atomic single-tick artifact emission.** An artifact and a tick
   are the same object. Multi-tick continuity is structurally
   unrepresentable.
2. **Verb-fused persona identity.** "Artisan" / "Scholar" name the
   agent by its production verb. The autoregressive prior latches
   on the verb itself.
3. **Flat selection gradient at Trader.** Same-model ranker,
   length-blind, no diversity term.

ADR 0002 named four future-move candidates (lowest-intervention
first): persona prompt revision, different model, multi-tick
artifact WIP via shared workshop state, and an `active_intent`
reserved field. This ADR addresses the third option: introduce a
shared, durable WIP stock that agents contribute to across many
ticks.

Codex pre-flagged three load-bearing risks for this design (full
transcript in the v0.2 plan):

- "External object memory" is only categorically different from
  "self-history" if the object is rendered without giving each agent
  their own prior text back. Without per-receiver redaction, the
  workshop is an autobiographical channel under a new name.
- A WIP table written separately from the episodic event log creates
  two commit surfaces; kill-safety (`scripts/verify_kill_drill.py`)
  breaks if a contribution event is committed but the WIP write is
  not, or vice versa.
- A Trader rewrite that exposes its scores back to agents creates a
  new attractor: agents converge on whatever scores well.

## Decision

Three load-bearing decisions, each phrased as an architectural
contract that tests pin:

### Decision 1 — Per-receiver redacted workshop views

`build_context(agent_X)` returns a `workshop_view` in which X's prior
fragments are rendered as anonymous `[earlier contributor]` markers;
non-X contributors are named verbatim. The receiver-name redaction
mirrors the existing lore-excerpt redaction
(`src/microverse/memory/__init__.py:241-243`). Path-3's structural
no-self-history guarantee is preserved: a structural leak sweep
parallel to PR #24's 897-sample sweep asserts zero substring matches
of any of the receiver's own fragment texts in their
`workshop_view`.

### Decision 2 — Episodic event log is the sole authoritative write

The workshop is a `WorkshopProjection`: a read-model over the
episodic event log. A `contribute` action writes one episodic event
(`actor=<agent>, action='contribute', target=<wip-name>,
payload={'fragment': ..., 'thought': ...}`); the projection's
in-memory state is then updated. On process restart the projection
is rebuilt from the event log; the in-memory cache is never trusted
standalone. Snapshots include the workshop projection implicitly
because the episodic SQLite file is snapshotted; on load the
projection is rebuilt from the log (no separate snapshot of the
projection itself). Kill-safety drill is reused unchanged.

### Decision 3 — Trader output never re-enters agent-visible context

The existing post-Layer-G removal of the `harvest.rated` synthetic
event (ADR 0002 reference data) is preserved. Trader rank scores
flow only into the harvest manifest, never into any subsequent
`WorldContext` for any agent. Per-primary-verb caps and floors are
post-generation filters applied in `Harvester.flush()`; they do not
feed back as upstream prompt signals. A leak sweep parallel to the
Path-3 pattern asserts no Trader verdict text appears in any later
`build_context()`.

### Implementation contract (additive, backward-compatible)

- `Action.contribute_to: str | None = None`. Default action behaviour
  unchanged when the field is None.
- `ActionKind.CONTRIBUTE = "contribute"`. `parse_action` validates
  `contribute_to` against the configured WIP names and folds invalid
  contributions to `rest` (preserves the "never raises" invariant).
- `WorldContext.workshop_view: tuple[WIPView, ...] = ()`. Default
  rendering is the no-op (no block, identical to v0.1.1).
- Configured WIP set at startup: `workshop.scroll`, `workshop.loom`,
  `workshop.garden_bed`. Phase transitions are deterministic
  (forming/developing/complete) based on contributor count, fragment
  count, and a stale-timeout. Agent-spawnable WIPs are explicitly
  out of scope for v0.2.

## Consequences

- WAL kill-safety preserved. The projection is derived state; the
  event log is the source of truth.
- Path-3 structural leak guarantee preserved. The new
  `workshop_view` channel is audited by a 24h structural-leak sweep
  before v0.2 ships.
- Multi-tick artifact accretion is now structurally representable.
  A "scroll currently shows 4 fragments by Bo, [earlier contributor]"
  is a coherent prompt input that a single-tick `craft` could never
  produce.
- Trader v2 is rule-based (length, contributor-count, Jaccard
  thematic-distance using the existing FTS5 lore index for
  tokenisation). No same-model "is this interesting" prompt; no
  upstream feedback loop.
- Persona reshape (ADR 0002 future-move #1) is reframed: instead of
  being a coercion patch, it lands LAST in the v0.2 phase order and
  is a consequence of new affordances rather than a precursor.
- The "Layer pattern" remains closed. Future workshop attractors are
  surfaced via the same leak-sweep machinery as v0.1.1; symptomatic
  prompt patches remain forbidden.

## Out of scope (v0.3+)

- Agent-spawnable WIPs.
- Persona evolution via Elder lore.
- `active_intent` reserved field (still YAGNI).
- Model swap to `gemma4:26b` — defer until v0.2 measurement is in
  hand so the comparison is apples-to-apples.

## Reference data (filled at promotion to Accepted)

- Phase 0 spike halt-criterion results (`scripts/spike_workshop_measure.py`).
- Phase 4 structural leak sweep (24h, seed 38).
- Phase 6 Trader-feedback-invisibility sweep.
- Final v0.2 acceptance soak (24h, seed 38) per the v0.2 plan halt
  criteria.
