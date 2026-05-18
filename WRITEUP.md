# Microverse Battery — A Private, Offline AI Society on One Laptop

## Subtitle

*A multi-agent simulation that ran 24 hours on a single Apple Silicon laptop,
entirely offline, on Gemma 4 via Ollama. 702 audited artifacts. Zero API
calls. Full provenance.*

---

## The hook

Most agent demos die when the Wi-Fi does. We ran one for 24 hours on a
single Apple Silicon laptop, entirely offline, and it produced 14,113 events
and 702 auditable artifacts — collaborative essays, design proposals, field
notes — through a single Gemma 4 model running locally via Ollama. No
hosted middleware. No per-token bill. No "thoughts" leaving the device.

The system is small enough to clone and run on your own machine in under
an hour. The provenance for every artifact — prompt input, model output,
ranking score, acceptance verdict — is on disk in SQLite. The dashboard is
a static HTML file with no JavaScript framework. You can read every
decision the system made, including the ones we got wrong, and the
Architecture Decision Records that drove the fixes.

## Why this wins the Ollama Special Track

- **One Gemma 4 model, one entry point.** Every persona — Artisan, Trader,
  Elder, Stranger — calls `gemma4:26b` through a single
  `ollama_client.chat()` function. No router. No fallback. No second model.
  The invariant is in `src/microverse/config.py:17` and tested in CI.
- **Ollama structured output is the Trader.** Artifact ranking uses
  Ollama's `format="json"` mode — one LLM call returns an ordered score
  list with rationales; the Harvester applies a percentile cutoff. This is
  the production-path use of an Ollama feature, not a demo.
- **Explicit thinking-channel discipline.** Gemma 4's reasoning trace is
  powerful but leak-prone. We pass `think=False`, force `thinking=""` on
  the response, scrub defensively, and bump a `thinking_leak` counter on
  any escape. After 24 hours, the counter is zero — we measure this as a
  Safety & Trust property, not assume it.
- **Zero cloud dependencies, reproducible from a clean clone.**
  `git clone && uv sync && ollama pull gemma4:26b && uv run python -m microverse.run`.
  That's the full setup. The included kill-drill script proves WAL
  recovery survives `SIGKILL`.

## A concrete use case

A rural clinic with intermittent connectivity needs intake summaries that
*never* leave the device. A multi-author classroom journal needs to keep
generating prompts during a network outage. A regulated-industry knowledge
team needs an agent loop that runs on the laptop on the locked desk, not
the data center across the network boundary. Microverse Battery is the
substrate for those applications: the durability, the observability, the
multi-agent collaboration, and the honest measurement all already work
offline. What changes is the persona prompts, not the architecture.

The demo we ship — a fictional society of agents collaborating on garden
beds, looms, and scrolls, with immigrant Strangers bringing imagined
perspectives from "coastal marshes" and "arid plains" — is a stress test,
not the product. The stress test produced 702 artifacts in 24 hours. The
product would be whatever specialized agent set the deployment needs.

## Architecture

The runtime is a single-process Python tick loop in `src/microverse/run.py`.
One tick:

1. A weighted scheduler picks an agent (weight = `soul_tokens`, floor 1).
2. A bounded `WorldContext` is assembled: recent episodic memory
   (≤1,500 tokens), FTS5 semantic-memory lore excerpt (≤600 tokens), the
   current weather event, and a filtered peer-inbox. Critically:
   **Path-3 stateless tick** — agents never see their own prior actions,
   so personality drift isn't reinforced by autobiographical replay.
3. The agent's `think()` call goes to the local Ollama client.
4. The returned JSON `Action` is parsed defensively
   (strict JSON → `json_repair` → safe-`rest` fallback; never raises).
5. The action commits to a SQLite WAL event log; the Harvester buffers
   candidate artifacts.
6. A `WorldClock` may emit a seeded weather event. Every 25 ticks the
   Watchdog checks for runaway, stagnation, diversity. Every 50 ticks the
   Harvester flushes its buffer through the Trader's ranking and writes
   accepted artifacts to `harvest/inbox/`.

Modules map cleanly: `agents/`, `memory/`, `world/`, `ops/`, `llm/`,
`prompts/`. Two SQLite databases are the substrate: `episodic.sqlite` (the
durability boundary, opened with `synchronous=NORMAL` and verified by a
kill-drill script) and `semantic.sqlite` (FTS5 lore recall, compressed
periodically by the Elder with a Jaccard drift guard against runaway
edits). The shared workshop (ADR 0003) is the multi-author artifact
substrate: agents `contribute_to` shared work-in-progress entries across
ticks, and the Trader scores the *aggregate* WIP, not individual
contributions.

## The ADR journey — breakthroughs, not a defect ledger

Five Architecture Decision Records document how we got here.

**ADR 0001 — Local-first runtime works.** Single-model invariant. SQLite
WAL durability. FTS5 semantic recall. The basic substrate held up under
load.

**ADR 0002 — We instrumented a model-level limit and shipped with it
documented.** After seven patches, the Artisan persona stabilized at a
~93% craft-share specialization that no prompt change moved. Instead of
hiding the limit, we measured it, wrote it into the v0.1 README, and
shipped. Honest measurement of what models actually do under load is the
Safety & Trust contribution.

**ADR 0003 — Collaboration substrate solved the solo bottleneck.** The
breakthrough was making artifact creation collective: a workshop holds
multi-author work-in-progress, agents build on each other across ticks,
the Trader scores the aggregate. The conversation gained structure
because the *substrate* gained structure. The artifacts in the live
dashboard are the result.

**ADR 0004 — Structural fixes closed throughput gates.** Four
structural-not-instructional fixes (capacity invariant, fragment-length
floor, recycle lifecycle, acceptance subfloor) plus a model swap to
`gemma4:26b` raised accepted-WIPs-per-hour from 3.7 to 29.3 — an 8x
throughput improvement on the same hardware.

**ADR 0005 — The next bottleneck is harness shape, and we know the
shape of the fix.** The same soaks that closed throughput exposed a
structural ceiling on social engagement: a single-action tick can't
guarantee peer reference because there's no prior turn to read. v0.4
replaces the workshop affordance with a 3-turn scene where turn N reads
turns 1..N-1. Engagement becomes the path of least resistance instead of
the path the prompt has to beg for. Acceptance gates are written before
the code lands.

Each ADR closed one bottleneck and exposed the next. The published
limits aren't defects — they're the load-bearing evidence that the next
fix is the right one.

## Demo and what's next

The live dashboard at
**https://theillusionoflife.github.io/microverse/** renders the metrics,
weather feed, and harvested artifacts from a 24-hour `gemma4:26b` soak —
14,113 events, 702 accepted artifacts, 9,502 successful JSON ops,
9,502/9,522 valid Action JSON (99.79%), zero thinking-channel leaks, all
generated locally over one wall-clock day on Apple Silicon.

Click any artifact and you'll see Aki proposing a parchment treatment
("dust the edges with alum to prevent the ink feathering") and a Stranger
responding with a perspective from her imagined homeland ("instead of
alum, we might try a technique from the coastal reaches…"). That cross-
turn responsiveness is what local-first multi-agent intelligence looks
like when the substrate gives the model room to be social.

The codebase, ADRs, soak data, and this writeup are at
**https://github.com/TheIllusionOfLife/microverse**. Clone it, `uv sync`,
`ollama pull gemma4:26b`, run your own society. The kill-drill works. The
dashboard renders. The provenance is complete from prompt input to ranked
output. No API key required, ever.

Next: ADR 0005's scenes ship as v0.4. If the acceptance soak closes the
peer-engagement gate, the same local stack becomes a credible substrate
for offline classroom co-pilots, regulated-industry knowledge digests,
and any application that needs autonomous agentic generation to keep
running when the network doesn't. The hard parts — durability,
observability, governance, honest measurement, Gemma 4's structured
output and thinking discipline — already work today on one laptop.
