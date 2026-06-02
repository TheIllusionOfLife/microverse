"""Action-economy scarcity lever — re-diagnosis spike for the ADR 0008 halt.

ADR 0008 halted identity Phase 1 because neither Gate 1 nor Gate 3 moved,
and diagnosed Gate 3 (verb monoculture) as *structural*: the scene/workshop
loop funnels ~95% of actions into ``contribute``, and "no identity layer can
diversify verbs without changing the action economy itself."

This module is the smallest viable slice of ADR 0007 Pillar 2 (scarcity +
comparative advantage) used to test that claim falsifiably. Each agent has a
finite stamina pool that regenerates per tick; each verb has a per-role cost
so roles are cheap at their specialty and dear elsewhere. When the LLM picks
a verb the agent cannot afford, the run loop substitutes the cheapest
affordable productive verb (see :func:`microverse.agents.base.apply_economy_lever`).

Invariants this module preserves:
  * **In-memory only.** The ledger is NEVER persisted; the WAL stays the
    durability boundary. A cold start resets every pool to ``max_energy``,
    identical to process start. Do not persist it — that would make energy a
    recovery dependency. Restart equivalence is handled by reconstructing the
    pool from the WAL at startup (see ``run.py``), not by storing it.
  * **No LLM calls.** The single-model invariant for ``agent.think()`` is
    untouched; this module never imports the Ollama client.

The whole lever is feature-flagged in ``config`` (``ECONOMY_MODE``): mode
``"0"`` constructs no ledger and reproduces current ``main`` behavior exactly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

CostTable = Mapping[str, Mapping[str, float]]

# Verbs that require an LLM-authored payload the lever cannot fabricate, so
# they are NEVER a substitution *target*: ``contribute`` needs a WIP + fragment,
# and ``craft`` needs an ``artifact``. Substituting toward either would commit a
# hollow action (``artifact=None``) that bypasses the empty-craft guard, is
# unharvestable, yet still counts as productive verb diversity in Gate 9 — i.e.
# the diagnostic could report improvement without any real work (review). A
# role whose specialty is a payload verb (the Artisan's ``craft``) therefore
# diversifies through the perception channel: ``energy_hint`` (mode ``adv``, via
# :meth:`EnergyLedger.cheapest_affordable_perceived`) names ``craft`` so the
# model can *choose* it and author the artifact, while the hard executor only
# ever falls back to payload-free verbs or ``rest``. NOTE: the legacy ``sub``
# hint named :meth:`cheapest_affordable_productive` (which excludes ``craft``),
# so it pointed the Artisan at the shared payload-free escape (``study`` — the
# Scholar's specialty) instead of its own ``craft`` — co-drift, not
# differentiation (ADR 0009). ``adv`` is the corrected, honest-hint arm.
_PAYLOAD_VERBS = frozenset({"contribute", "craft"})

# Excluded from the *primary* substitution candidates: the payload verbs (above)
# plus ``rest``, which is held back as the last resort so the lever drives
# specialization toward an affordable productive verb rather than collapsing
# every constrained tick onto rest.
_SUBSTITUTION_EXCLUDED = _PAYLOAD_VERBS | {"rest"}

# Verbs excluded from the flat-control flattening: ``contribute`` keeps its
# per-role cost (so the scene-initiation throttle is identical across arms) and
# ``rest`` stays free. Everything else — including ``craft`` — collapses to the
# shared per-role cost so the flat arm genuinely removes the specialty.
_NON_SUBSTITUTABLE = frozenset({"contribute", "rest"})


def derive_flat_table(table: CostTable) -> dict[str, dict[str, float]]:
    """Role-agnostic control variant of ``table`` (Codex confound control).

    Keeps each role's ``contribute`` cost (so the scene-initiation throttle is
    IDENTICAL to the role-advantage arm) and ``rest`` (free), but flattens the
    four remaining productive verbs to a single shared per-role cost (their
    mean). This removes the comparative advantage while holding everything
    else constant, isolating "specialization diversifies" from "a uniform
    contribute throttle diversifies".
    """
    flat: dict[str, dict[str, float]] = {}
    for role, costs in table.items():
        others = [v for v in costs if v not in _NON_SUBSTITUTABLE]
        shared = sum(costs[v] for v in others) / len(others) if others else 0.0
        flat[role] = {
            v: (0.0 if v == "rest" else costs[v] if v == "contribute" else shared) for v in costs
        }
    return flat


def build_cost_table(mode: str) -> dict[str, dict[str, float]]:
    """Resolve the per-mode cost table.

    ``"flat"`` returns the role-agnostic control; every other economy mode
    (``"1"``, ``"sub"``, ``"throttle"``, ``"adv"``) uses the role-advantage
    table. ``"adv"`` shares ``"sub"``'s costs and differs only in the
    ``energy_hint`` selector (see ``run._compute_energy_hint``).
    """
    from microverse.config import VERB_COST_BY_ROLE

    if mode == "flat":
        return derive_flat_table(VERB_COST_BY_ROLE)
    return {role: dict(costs) for role, costs in VERB_COST_BY_ROLE.items()}


class EnergyLedger:
    """Per-agent finite stamina with per-tick regen. In-memory only.

    ``cost_table`` maps ``role -> {verb: cost}``. An unknown role or verb costs
    0.0 (no constraint) so a future role without an entry behaves as if the
    economy is off for it, never crashing the tick loop.
    """

    def __init__(
        self,
        *,
        max_energy: float,
        regen_per_tick: float,
        cost_table: CostTable,
    ) -> None:
        self.max_energy = float(max_energy)
        self.regen_per_tick = float(regen_per_tick)
        self._cost: CostTable = cost_table
        self._pool: dict[str, float] = {}

    @classmethod
    def fresh(
        cls,
        names: Iterable[str],
        *,
        max_energy: float,
        regen_per_tick: float,
        cost_table: CostTable,
    ) -> EnergyLedger:
        ledger = cls(max_energy=max_energy, regen_per_tick=regen_per_tick, cost_table=cost_table)
        for name in names:
            ledger._pool[name] = ledger.max_energy
        return ledger

    def current(self, name: str) -> float:
        """Energy for ``name``; an unseen agent (e.g. a Watchdog-spawned
        Stranger) starts full."""
        return self._pool.setdefault(name, self.max_energy)

    def cost(self, role: str, verb: str) -> float:
        role_costs = self._cost.get(role)
        if role_costs is None:
            return 0.0
        return float(role_costs.get(verb, 0.0))

    def can_afford(self, name: str, role: str, verb: str) -> bool:
        return self.current(name) >= self.cost(role, verb)

    def deduct(self, name: str, role: str, verb: str) -> None:
        self._pool[name] = max(0.0, self.current(name) - self.cost(role, verb))

    def regen(self, name: str) -> None:
        self._pool[name] = min(self.current(name) + self.regen_per_tick, self.max_energy)

    def affordable_verbs(self, name: str, role: str, candidates: Iterable[str]) -> list[str]:
        return [v for v in candidates if self.can_afford(name, role, v)]

    def reconstruct_from_events(self, events: Iterable[tuple[str, str, str]]) -> None:
        """Approximate restart reconstruction from the WAL (Codex #5).

        Energy is never persisted, so a naive restart resets every pool to
        ``max_energy`` and an interrupted economy-on run would diverge from an
        uninterrupted one. To keep restart behavior WAL-derived and
        deterministic, replay committed agent actions in chronological order,
        regen-then-deduct per actor event.

        This is intentionally APPROXIMATE: whole-roster per-tick regen is
        collapsed to per-own-action regen, so bit-equality across restart is
        NOT guaranteed (energy is a soft scheduling signal, not a durability
        boundary — the WAL remains the only durability contract). ADR 0009
        records that economy-on runs are not bit-identical across restart.

        ``events`` yields ``(actor, role, verb)`` tuples oldest-first. Order is
        deduct-then-regen per event, matching the live single-tick order
        (deduct on commit, then regen); it still cannot replicate the live
        whole-roster per-tick regen exactly, hence "approximate".
        """
        for actor, role, verb in events:
            self.deduct(actor, role, verb)
            self.regen(actor)

    def cheapest_affordable_productive(self, name: str, role: str) -> str | None:
        """The cheapest affordable substitution target, or ``None`` if the agent
        can afford none (then the lever falls back to rest).

        Candidates exclude the payload verbs (``contribute``, ``craft`` — the
        lever cannot fabricate their payloads) and ``rest`` (the last resort),
        so the realistic targets are ``speak``/``study``/``travel``. Ties are
        broken by ``ActionKind`` declaration order for determinism.
        """
        from microverse.agents.base import ActionKind

        order = [k.value for k in ActionKind]
        productive = [v for v in order if v not in _SUBSTITUTION_EXCLUDED]
        affordable = self.affordable_verbs(name, role, productive)
        if not affordable:
            return None
        return min(affordable, key=lambda v: (self.cost(role, v), order.index(v)))

    def cheapest_affordable_perceived(self, name: str, role: str) -> str | None:
        """The cheapest affordable productive verb to name in the ``energy_hint``
        (mode ``adv``), or ``None`` if the agent can afford none (then the hint
        falls back to the "rest before ..." message).

        Unlike :meth:`cheapest_affordable_productive` — the blind EXECUTOR's
        substitution target, which excludes the payload verbs it cannot
        fabricate (``contribute``/``craft``) — this is a PERCEPTION signal to a
        model that, when it *chooses* ``craft``, authors a real artifact. So it
        excludes only ``rest`` (the idle last resort) and may name a role's
        payload specialty. This is what lets the Artisan's hint name ``craft``
        (cost 6) rather than the shared payload-free escape ``study`` (the
        Scholar's specialty) — the fix for the ADR 0009 co-drift. Ties are
        broken by ``ActionKind`` declaration order for determinism, identical to
        the executor selector, so under the flat control (no specialty) both
        roles resolve to the same verb and the fix is inert.
        """
        from microverse.agents.base import ActionKind

        order = [k.value for k in ActionKind]
        productive = [v for v in order if v != "rest"]
        affordable = self.affordable_verbs(name, role, productive)
        if not affordable:
            return None
        return min(affordable, key=lambda v: (self.cost(role, v), order.index(v)))

    def resolve_executed_verb(self, name: str, role: str, chosen_verb: str) -> str:
        """Verb-level core of the economy lever, shared with the offline
        replay/simulator (``scripts/replay_economy.py``) so Stage-0/1 estimates
        match live behavior exactly. Returns ``chosen_verb`` when affordable;
        otherwise the cheapest affordable productive verb, or ``"rest"`` if
        even that is out of reach. Never substitutes toward ``contribute``."""
        if self.can_afford(name, role, chosen_verb):
            return chosen_verb
        cheapest = self.cheapest_affordable_productive(name, role)
        return cheapest if cheapest is not None else "rest"
