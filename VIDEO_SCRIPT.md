# Microverse Battery — Video Script (3 minutes)

**Target track:** Gemma 4 Hackathon, Ollama Special Track ($10K).
**Format:** 3 min ≤ 180s. ~450 words of voiceover. Cuts every 8–15s.
**Tone:** Confident, specific, no hype. Numbers over adjectives.

---

## Shot list (record before final cut)

| # | Shot | Capture method | Notes |
|---|------|----------------|-------|
| A | Title card "Microverse Battery — Local AI Society on Gemma 4" | Static slide | Use Keynote / Figma. 16:9. |
| B | Terminal: `uv run python -m microverse.run --ticks 100 --tempo 0 --seed 42` against `gemma4:26b` | QuickTime screen rec or asciinema | Run ahead of time so Ollama is warm. Capture the tick output and a few `[harvester] accepted` lines. ~30s of raw footage. |
| C | Slide: "One model. One entry point. No router." with `MODEL = "gemma4:26b"` from `src/microverse/config.py:17` | Static slide | Quote 3 lines of code on screen. |
| D | Dashboard walkthrough at https://theillusionoflife.github.io/microverse/ | Browser screen recording | Slow scroll. Pause on Residents table (6 agents), Metrics (9,502 json_ok), Weather events. |
| E | Zoom into one harvested artifact in the dashboard — the `garden-bed-22.md` workshop entry showing Aki + 2 Strangers collaborating | Browser zoom + cursor highlight | Read 1-2 lines aloud. |
| F | Slide: "5 ADRs. 4 bottlenecks closed. v0.4 acceptance gates written before code." Use the ADR titles as a vertical list. | Static slide | Visual cadence: each ADR appears one at a time. |
| G | Closing slide: GitHub URL, live dashboard URL, "Apple Silicon · Ollama · Gemma 4" | Static slide | 3s hold. |

---

## Voiceover script (timestamps assume 180s total)

### 0:00 — 0:15  [Shot A → Shot B starting]

> Most AI agent demos die when the Wi-Fi does.
>
> This one ran for 24 hours on a single laptop, entirely offline, on
> Gemma 4 through Ollama. No hosted middleware. No per-token bill.
> No "thoughts" leaving the device.

### 0:15 — 0:35  [Shot B continues — terminal scrolling]

> Microverse Battery is a multi-agent simulation. A society of personas —
> an Artisan, a Trader, an Elder, immigrant Strangers — generates
> artifacts over a long tick loop. Every call goes through a single Ollama
> chat to one Gemma 4 model. There is no router. There is no fallback.

### 0:35 — 0:55  [Shot C — code slide, then back to terminal]

> The single-model invariant lives in seventeen lines of config. Every
> persona — Artisan, Trader, Stranger — calls the same `gemma4:26b` model
> through one entry point. We use Ollama's structured-output JSON format
> for the Trader's artifact ranking, and we explicitly suppress Gemma's
> reasoning channel so it doesn't leak into the output. Twenty-four
> hours of soaking, zero thinking-channel leaks.

### 0:55 — 1:35  [Shot D — dashboard walkthrough]

> This is the live dashboard. It's a static HTML page — no JavaScript
> framework, no backend. Every artifact you see was generated on one
> Apple Silicon laptop over one wall-clock day.
>
> Fourteen thousand events. Seven hundred and two accepted artifacts.
> Nine thousand five hundred valid JSON Actions. Six resident agents,
> three of them immigrant Strangers spawned by a Watchdog when the
> conversation got stale.

### 1:35 — 2:05  [Shot E — zoom on artifact]

> Click into one. Here, Aki — the resident Artisan — proposes a parchment
> treatment for a shared scroll. A Stranger answers with a technique from
> her imagined coastal homeland. Another Stranger overlays a third
> approach from the arid plains. The Trader scored the aggregate
> workshop entry, the Harvester accepted it, the provenance is on disk.
>
> This is what local multi-agent intelligence looks like when the
> substrate gives the model room to be social.

### 2:05 — 2:35  [Shot F — ADR slide]

> The project earned five Architecture Decision Records along the way.
> Each one closed a bottleneck and exposed the next. ADR 1: the
> local-first runtime works. ADR 2: we documented a model-level limit
> instead of hiding it. ADR 3: shared workshops solved the solo-agent
> bottleneck. ADR 4: structural fixes raised throughput eight times. ADR
> 5: the v0.4 multi-turn scene proposal — the gates are written before
> the code.

### 2:35 — 3:00  [Shot G — closing slide]

> Microverse Battery proves that the hard parts of multi-agent AI —
> durability, observability, governance, honest measurement — already
> work today on one laptop with Gemma 4 and Ollama. No API key required,
> ever.
>
> Clone the repo. Pull the model. Run your own society.

---

## Production notes

- **Don't go over 3:00.** YouTube auto-displays "3:00" not "3:00.5". Trim
  the closing call-to-action by a beat if needed.
- **Background music:** optional, low. Voiceover is the load-bearing part.
- **Captions:** the script is dense; auto-captions will mangle "Ollama"
  and "Gemma." Upload an SRT.
- **Thumbnail:** use the dashboard cover (`docs/cover.png`) — same
  visual continuity as the Kaggle preview card.
- **Hosting:** YouTube, set to "Public" (judges must view without login).
  Copy the watch URL into the Kaggle writeup's Project Links section.
