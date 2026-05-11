# ADR 0002: Model-Level Attractor Constraint for `gemma4:e4b`

## Status

Accepted. Ships with v0.1.0.

## Context

PRs #17 through #23 ("Layer A" through "Layer G") each attempted to fix a
specific introspective trap observed in long-soak runs. Each layer reduced
one expression of the trap (rest streaks, empty crafts, silent-craftsperson
narrative, recursive synthesis around "completion"), and each layer was
followed by a soak that surfaced a new attractor through whichever
autobiographical channel the prior fix left open.

The Layer-G post-deploy 24h soak (`data/soak-24h-6`, hour 4) crystallised the
final variant: artifact text feeding back through `_format_episodic_event`'s
craft branch reached 91% completion vocabulary; Cy reached 84% craft share
producing near-duplicate "synthesise Aki's final pieces" thoughts. Codex
review of the Layer-G plan had flagged: "any patch invites re-route."

PR #24 ("Path 3") was a structural change rather than another layer. The
agent's per-tick prompt no longer contains any of its own past
(`recent_episodic` removed; `_format_episodic_event`, `_compress_action_runs`,
and `_pack_under_budget` deleted). Self-history is persisted to the episodic
SQLite log for durability, watchdog, harvest, and audit, but it never feeds
back into a prompt. The prompt sees only static persona, current world state
(`weather` derived per-tick), peer presence, a bounded peer-inbox (one-shot
drain, with whole-word case-insensitive name filter on the utterance), bounded
world-events (`actor='world'` only), lore-excerpt (FTS5 keyed on weather, with
per-receiver name redaction), and an exogenous engagement nudge.

The 24h Path-3 soak (`data/soak-24h-pr24`, seed 38, 22,614 events) was meant
to verify whether removing the substrate also removes the attractor.

## Decision

Ship v0.1.0 as a harness with a documented model-level attractor constraint.
Do NOT add a "Layer I" patch.

The Path-3 soak shows two distinct results:

1. **The architecture works.** A 24h, 897-sample structural leak sweep
   returned zero substring matches of any agent's prior thought, artifact,
   action-verb pattern, or self-name lore line across every text field of
   the assembled `WorldContext`. Cross-agent narrative laundering through
   `peer_inbox` is bounded by the whole-word name filter (which fired 77+ in
   the wiring soak and continues to be load-bearing in the 24h run). Lore
   excerpts containing the receiver's own name are dropped at retrieval
   time. Weather and engagement-gate signals surface correctly.

2. **The model still has a primary-verb attractor.** Aki (Artisan) settled
   at 93-94% craft share within hour 1 and held that distribution across
   every hour of the 24h run. This crosses the conservative halt sentinel
   (any single action > 70% sustained two consecutive hours AND worse than
   the Layer-G baseline of 46% for Aki). The trap is *not* the Layer-G
   silent-craftsperson failure: Aki's manifest grew to 19,918 lines at
   ~830 lines/hour (three times the Layer-G rate of ~283/hour), thought
   diversity stayed at 0.86 (matching the Layer-G 0.92), Cy stayed varied
   (study 59-71%, rest and speak interspersed, diversity 0.87, never
   exceeded its baseline 84% craft share), and the engagement gate
   continued to fire and coerce. Aki is producing real, distinct artifacts
   at high volume — the LLM has specialised on its persona's primary verb,
   not collapsed into recursion.

The Layer-G plan stated: "if the post-R1+R2 24h soak shows yet another
route-around, the honest response is NOT a Layer H patch. It is to admit
that gemma4:e4b at this size has an attractor we cannot fully eliminate
without a different model or a fundamentally different prompt architecture."
Path-3 *is* the fundamentally different prompt architecture. The remaining
attractor is the model itself: `gemma4:e4b` (8B parameters, Q4_K_M) loves
its persona's primary verb and will lean into it absent narrative pressure
to do otherwise.

## Consequences

- v0.1.0 ships as documented. The harness is correct: events are durable,
  prompts contain no autobiographical leakage, peer interaction works, the
  engagement gate works, weather surfaces, lore retrieval works, harvest
  ranks and writes accepted artifacts atomically.

- Users running `gemma4:e4b` on the default Artisan + Scholar roster should
  expect Aki to specialise heavily on craft (90%+) and Cy to remain varied
  but study-leaning. This is a feature characteristic, not a defect.

- The Layer pattern stops here. Further attempts to coerce the action
  distribution by patching prompt text would be churn: the experiments
  across PRs #17-23 show that any single corrective channel becomes the
  next attractor's substrate. The architectural mitigations in PR #24 are
  the structural ceiling.

- Future moves that *could* change this finding, but are outside v0.1.0:
  - **Different model.** A larger (`gemma4:26b`) or differently-tuned model
    may distribute actions more evenly under the same persona prompts.
    Worth verifying with a one-off comparison soak.
  - **Persona prompt revision.** The current Artisan persona over-specifies
    craft. A revision that frames the role more broadly ("inhabitant who
    sometimes makes things") may produce a flatter distribution. This is a
    v0.2 concern.
  - **Multi-tick artifact WIP.** Out-of-scope for v0.1.0 per the Path-3
    plan; a v0.2 "shared workshop state" abstraction could change the
    incentive landscape.
  - **`active_intent` reserved field on `WorldContext`.** Considered and
    skipped per YAGNI in the Path-3 plan. Available as a v0.2 hook.

- Test/CI obligations are unchanged: the default suite stays offline; live
  soaks remain operator-run; soak data dirs (`data/soak-*`) and harvest
  outputs (`harvest/soak-*`) remain untracked.

## Reference data

- Layer-G baseline (`data/soak-24h-6`, 4.16h): Aki 46% craft / 0.92
  diversity; Cy 84% craft / 0.89 diversity; manifest 1,179 lines.
- Path-3 24h (`data/soak-24h-pr24`, 24.0h): Aki 93% craft / 0.86 diversity;
  Cy varied (study 59-71%) / 0.87 diversity; manifest 19,918 lines;
  zero structural leaks across 897 sampled `WorldContext` snapshots.
