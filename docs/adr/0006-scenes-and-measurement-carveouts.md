# ADR 0006: Scenes, Embeddings, and the Verb-Diversity Lever (v0.4 → v1.0)

## Status

Proposed. Targets `v1.0.0`. Lands the three v0.4 levers that ADR 0005
named (Decisions 2 and 3) plus the verb-diversity counter-pressure
that ADR 0002 left as a documented limit. The acceptance evidence is
the 7-day operator soak; this ADR commits the contracts and carve-outs
the code already implements.

## Context

ADR 0005 left three things in a defined state but unshipped:

1. **Decision 2** — transition-triggered harvest flush. Shipped in
   Phase B: `WorkshopProjection.drain_complete_transitions()` is an
   edge-triggered set; `run.py` consults it each tick and flushes
   subject to a 5-tick throttle. Two new counters distinguish trigger
   cause: `harvest_flush_timer_triggered`,
   `harvest_flush_transition_triggered`.

2. **Decision 3** — multi-turn scenes. Shipped in Phase C:
   `microverse.world.scene.SceneRunner` drives a 3-turn sequence on a
   workshop WIP, gated at `config.SCENE_GATE_P = 0.15`. The event
   contract is `scene.open` + 3 `contribute` events tagged with
   `scene_id` and `turn_index` + an optional `scene.abort` on failure.

3. **Gate 7 (semantic dependence)** — requires embeddings, which the
   project did not have. Shipped in Phase C: `llm/embeddings.py`
   calls `ollama.embed(model=EMBEDDING_MODEL)` with a SHA-256-keyed
   `lru_cache`. **Never called from `agent.think()`**.

ADR 0005 also explicitly deferred a verb-diversity lever for ADR 0002's
attractor. Per user direction at planning time we shipped both
sub-levers (persona hint + post-action substitution at 30%) in Phase D
as structural counter-pressure, not a claim to dissolve the model-level
limit.

## Decision

The contracts below are now load-bearing for `v1.0.0`. Any future
change must preserve them.

### Scene event contract

`microverse.world.scene.SceneRunner.run(initiator, wip_name, peers)`
emits the following sequence into the episodic log:

```
scene.open  payload={scene_id, turn1_author, turn2_author,
                     turn3_author, wip_name}
contribute  payload={scene_id, turn_index=1, fragment, ...}    [turn 1]
contribute  payload={scene_id, turn_index=2, fragment, ...}    [turn 2]
contribute  payload={scene_id, turn_index=3, fragment, ...}    [turn 3]
```

On any failure mid-scene (think raises, parsed action is not a
contribute to `wip_name`, or commit raises):

```
scene.abort payload={scene_id, last_turn, reason}
```

Partial contributes that landed before the abort stay in the WIP. The
harvester treats the partial WIP under its existing acceptance rules.

**Replay determinism**: The scheduler's RNG is not checkpointed across
restart, so author selection MUST be read from the `scene.open` event,
not re-computed via `sched.next()`. The runner emits `scene.open`
BEFORE any `think()` to make this contract enforceable.

**Projection invariance**: `WorkshopProjection._apply()` ignores
`scene.open` and `scene.abort` events; they are read by other consumers
(gate-8 producer, kill-drill verifier) but never affect WIP state.
The WIP grows fragment-by-fragment from the 3 contributes regardless
of whether they're scene-tagged.

### Turn-3 same-author carve-out (Path-3 boundary)

When the available peer pool yields only one distinct peer, the
rotation falls back to A→B→A (turn 3 closes back to the initiator).
ADR 0003 Path-3 says agents must NOT see their own prior actions in
their prompt. The scene carve-out: turn 3 (with author == turn 1
author) DOES see turn 1's text via `WorldContext.scene_context` —
because `scene_context` is an explicit scene-scoped input, not
autobiographical replay.

The boundary is:

- `WorldContext.scene_context: tuple[SceneTurn, ...]` — explicit scene
  input, always surfaced regardless of author, only populated during
  active scenes by `SceneRunner._build_scene_context`.
- `_build_workshop_view` (memory/__init__.py) — autobiographical
  filter, still redacts the same agent's own fragments from a WIP
  excerpt.

A test (`test_turn3_same_author_sees_own_turn1_in_scene_context`)
guards the carve-out.

### Embedding model — single-model invariant carve-out

`config.EMBEDDING_MODEL = "nomic-embed-text"` is a SECOND model in the
runtime, but only for **measurement infrastructure**:

- Called only from `scripts/spike_workshop_measure.py`
  (`gate8_scene_semantic_dependence`).
- Never imported by anything in `microverse.agents.*` or
  `microverse.run`.
- A test guard (`test_embed_returns_empty_on_failure`) confirms gate
  measurement degrades to "unavailable" rather than crashing when the
  embedding model isn't pulled.

The single-model invariant (`microverse.config.MODEL`) for the **agent
action loop** is preserved.

### Verb-diversity lever (Phase D, ADR 0002 counter-pressure)

Two steps, both ship together:

- **Step 1 — persona hint.** `_compute_novelty_hint(episodic, agent)`
  in `run.py` computes the dominant verb in the agent's most recent
  200 actions; if share > 0.50, builds a hint of the exact form
  `"You have leaned heavily on X lately; consider Y."` and surfaces
  it via `WorldContext.novelty_hint`. Persona templates render it
  as one line.

- **Step 2 — post-action substitution.** In each agent's `think()`,
  `_maybe_diversify(action, world)` re-parses the hint, and when the
  LLM emitted X anyway, flips a `random()` coin against
  `_DIVERSIFY_PROB = 0.30`. On fire, substitutes the action verb
  with Y, drops the original thought, bumps
  `diversity_lever_substituted` (per-agent).

Carve-outs (codified, not negotiable):

- Fallback REST (empty thought) is never substituted — masking would
  hide JSON-failure signal.
- CONTRIBUTE is never the substitution target — substituting blindly
  would hard-fold in the validator (no WIP target, no fragment).
- When the LLM already picked a verb other than X, the lever does
  not fire even if the hint is set.

This is structural counter-pressure equivalent to F.2 and the
engagement-gate. It does NOT claim to dissolve ADR 0002.

## Consequences

- Replay determinism extends to scene events (`scene.open` is the
  authority for author selection).
- The kill-drill verifier (`scripts/verify_kill_drill.py`) grew a
  `--scene-boundary` flag that asserts no orphan `scene.open` (open
  with neither completing contributes nor an abort).
- The gates producer (`scripts/spike_workshop_measure.py`) grew
  `gate_8_scene_semantic_dependence`. It complements the existing
  gate 1 (peer-reference rate, lexical) with a semantic check
  (cosine in [0.30, 0.85]) that catches scenes passing gate 1 by
  lexical fluke.
- The dashboard (`scripts/render_dashboard.py`) globs
  `manifest*.jsonl` so rotated audit archives still surface in the
  most-recent view.
- The persona templates are unchanged structurally: the new fields
  (`scene_context`, `novelty_hint`) are additive blocks with
  conservative `{% if %}` guards. Pre-Phase-C and pre-Phase-D fixtures
  continue to render correctly.
- ADR 0002 is NOT closed by this ADR. The verb-diversity lever adds
  structural pressure; the model-level attractor's persistence
  remains a documented limit.
- The single-model invariant for `agent.think()` is preserved. The
  embedding model is an observability tool. A reviewer who reads
  `agent.think` will still see exactly one `chat()` call, to
  `config.MODEL`.

## Out of scope

- A cross-family experiment (Qwen, Llama). User direction at planning
  time was to stay on `gemma4:26b`; ADR 0005's "deferred separate
  variable" framing is preserved unchanged.
- Restoring `gemma4:e4b` as a first-class production tier. The
  smaller model still works (24h soak per ADR 0004) but `26b` is the
  production default through `v1.0.0`.
- Digest-style alternative artifacts (the ADR 0005 fallback if gate 1
  fails entirely under scenes). Pre-baked halt criteria in the
  v1.0 plan re-raise this if the acceptance soak forces it.

## Reference

- Plan: `/Users/yuyamukai/.claude/plans/ok-so-do-everything-abundant-seahorse.md`
- Parent: ADR 0005 (proposed scenes; this ADR ships them).
- Predecessor: ADR 0002 (verb attractor; this ADR adds counter-pressure).
- Predecessor: ADR 0003 (workshop substrate + Path-3; scene carve-out
  defined relative to it).
