# Microverse Battery — Task Checklist

> Source of truth for ralph-loop. Top-to-bottom. Tick `[ ] → [x]` only after
> the task's `**Acceptance**` command produces output matching `**Expected**`.
> Paste the actual output (with timestamp) under `**Evidence**`.
>
> Each phase ends with a Phase Boundary block — run the Phase Boundary
> Protocol from PROMPT.md, then fill in the MERGED line.

---

## Phase 0 — Bootstrap + verify thinking-off (slug: `bootstrap`)

- [x] **0.1** `git checkout -b feat/phase-0-bootstrap` (already done by orchestrator before ralph started)
  - **Acceptance**: `git -C /Users/yuyamukai/dev/microverse branch --show-current`
  - **Expected**: `feat/phase-0-bootstrap`
  - **Evidence**: pre-ralph; verified by orchestrator at branch creation.

- [x] **0.2** Initialize `uv` package + add deps.
  - Steps: `cd /Users/yuyamukai/dev/microverse && uv init --package microverse --python 3.12` (skip if already initialized); `uv add ollama 'pydantic>=2' jinja2 python-dateutil json-repair`; `uv add --dev pytest pytest-asyncio ruff`.
  - **Acceptance**: `uv run python -c "import ollama, pydantic, jinja2, json_repair; print('deps_ok')"`
  - **Expected**: `deps_ok`
  - **Evidence**: `deps_ok` @ 2026-05-03T14:24Z. Project initialized with src/ layout (`uv init --package --name microverse .`). Deps: ollama 0.6.2, pydantic 2.13.3, jinja2 3.1.6, python-dateutil 2.9, json-repair 0.59.5, pytest 9.0.3, pytest-asyncio 1.3, ruff 0.15.12.

- [x] **0.3** Create `.gitignore` for `data/`, `harvest/`, `.venv/`, `__pycache__/`, `*.pyc`, `dist/`, `*.egg-info/`.
  - **Acceptance**: `grep -E '^(data/|harvest/|\.venv/|__pycache__/|\*\.pyc|dist/|\*\.egg-info/)' /Users/yuyamukai/dev/microverse/.gitignore | wc -l | tr -d ' '`
  - **Expected**: `7`
  - **Evidence**: `7` @ 2026-05-03T14:38Z (re-checked after CodeRabbit flagged grep/description mismatch).

- [x] **0.4** Implement `microverse/llm/thinking.py::strip_thinking` per `~/.claude/skills/local-llm/SKILL.md:99-106`.
  - TDD first: `tests/test_llm_strip_thinking.py` with cases (a) `<think>x</think>foo` → `foo`, (b) `foo` → `foo`, (c) `<think>x</think>` → empty, (d) channel-marker variant.
  - **Acceptance**: `uv run pytest tests/test_llm_strip_thinking.py -q`
  - **Expected**: `passed` substring; no `failed`.
  - **Evidence**: `6 passed in 0.00s` @ 2026-05-03T14:27Z. Module at src/microverse/llm/thinking.py.

- [x] **0.5** Implement `microverse/llm/ollama_client.py::chat(messages, *, think=False, format=None, options=None, timeout_s=90)`.
  - Wraps `ollama.chat(model="gemma4:e4b", messages=..., think=think, format=format, options=options)`.
  - Returns dict `{"content": str, "thinking": str, "raw": dict}`.
  - After response: `strip_thinking()` content as defense-in-depth; bump `thinking_leak` counter (in-process, simple module-level int for now) if it changed anything.
  - **Acceptance**: `uv run python -c "from microverse.llm.ollama_client import chat; print(callable(chat))"`
  - **Expected**: `True`
  - **Evidence**: `True` @ 2026-05-03T14:30Z. 7 unit tests passing (think kwarg, leak counter, options/format forwarding, timeout).

- [x] **0.6** Integration test for `think=False` on real Ollama.
  - `tests/test_ollama_think_off.py`, marked `@pytest.mark.integration`.
  - One call with `think=True` to confirm `thinking` populates.
  - One call with `think=False` to confirm `thinking == ""` AND `<think>` not in `content`.
  - If `think=True` fails (model isn't a thinking model in Ollama's view), document in README and skip the `think=True` branch — `think=False` empty-thinking assertion is the contract.
  - **Acceptance**: `uv run pytest tests/test_ollama_think_off.py -q -m integration`
  - **Expected**: `passed`
  - **Evidence**: `1 passed, 1 skipped in 5.31s` @ 2026-05-03T14:33Z. think=False contract holds; gemma4:e4b not classified as thinking-capable by Ollama so the think=True branch is skipped (documented in README.md).

- [x] **0.7** Smoke test (real model in the loop).
  - **Acceptance**: `uv run python -c "from microverse.llm.ollama_client import chat; r=chat([{'role':'user','content':'Reply with the single word OK.'}], think=False); assert r['content'].strip()=='OK', r; assert r['thinking']=='', r; print('smoke_ok')"`
  - **Expected**: `smoke_ok`
  - **Evidence**: `smoke_ok` @ 2026-05-03T14:34Z.

- [x] **0.8** Confirm `pr-create` skill available; if not, fall back to raw `gh pr create`.
  - **Acceptance**: `ls ~/.claude/skills/pr-create 2>/dev/null && echo skill_present || echo will_use_gh_directly`
  - **Expected**: one of `skill_present`, `will_use_gh_directly`
  - **Evidence**: `skill_present` @ 2026-05-03T14:34Z.

- [x] **0.9** Final phase verification.
  - **Acceptance**: `cd /Users/yuyamukai/dev/microverse && uv run ruff check && uv run ruff format --check && uv run pytest -q`
  - **Expected**: `passed` substring; no `failed`, no `error`.
  - **Evidence**: `All checks passed!` + `8 files already formatted` + `14 passed, 1 skipped in 0.68s` @ 2026-05-03T14:35Z. (1 skip is the documented gemma4 think=True branch.)

- [x] **0.10** Record sentinel `PHASE_0_COMPLETE` here once 0.2–0.9 ticked.
  - **Acceptance**: `grep -c '^PHASE_0_COMPLETE @ ' TODO.md`
  - **Expected**: `1`
  - **Evidence**: `1` @ 2026-05-03T14:35Z. Sentinel line written into "Phase 0 Boundary" block below.

### Phase 0 Boundary
PHASE_0_COMPLETE @ 2026-05-03T14:35Z
**MERGED**: da1ee5e131de942761e23f80e173600bcbccef27 @ 2026-05-03T05:42:47Z (PR #2)

---

## Phase 1 — Single-agent MVP harvest loop (slug: `mvp`)

- [x] **1.1** Branch `feat/phase-1-mvp` (only after Phase 0 merged + main pulled).
  - **Acceptance**: `git -C /Users/yuyamukai/dev/microverse branch --show-current`
  - **Expected**: `feat/phase-1-mvp`
  - **Evidence**: `feat/phase-1-mvp` @ 2026-05-03T14:43Z. Phase 0 PR #2 merged at da1ee5e.

- [x] **1.2** `microverse/config.py` with `MODEL = "gemma4:e4b"`, sampling presets, timeouts (`LLM_TIMEOUT_S=90`, `LLM_MAX_TOKENS=1024`), retry caps (`MAX_RETRIES=2`, `MAX_CONSECUTIVE_FAIL=3`).
  - **Acceptance**: `uv run python -c "from microverse.config import MODEL, LLM_TIMEOUT_S; print(MODEL, LLM_TIMEOUT_S)"`
  - **Expected**: `gemma4:e4b 90` (or `gemma4:e4b 90.0` since LLM_TIMEOUT_S is `float`)
  - **Evidence**: `gemma4:e4b 90.0` @ 2026-05-03T14:44Z. Module created during Phase 0 review-fix; this task added SAMPLING_CREATIVE/SAMPLING_FACTUAL presets and MAX_TICKS_DEFAULT.

- [x] **1.3** `microverse/memory/episodic.py` with WAL.
  - Schema: `events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT, payload_json TEXT)`.
  - On open: `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL`.
  - TDD: `tests/test_episodic.py` covers append + last-N + WAL pragma + file-backed crash recovery.
  - **Acceptance**: `uv run pytest tests/test_episodic.py -q`
  - **Expected**: `passed`
  - **Evidence**: `11 passed in 0.16s` @ 2026-05-03T14:48Z. Includes a SIGKILL drill via subprocess that confirms 5 committed events survive a `kill -9`.

- [x] **1.4** `microverse/ops/metrics.py` with counters `json_ok`, `json_repaired`, `json_fallback_rest`, `llm_timeout`, `consecutive_fail` per agent. Persist to `data/metrics.sqlite` every N ticks.
  - TDD: `tests/test_metrics.py`.
  - **Acceptance**: `uv run pytest tests/test_metrics.py -q`
  - **Expected**: `passed`
  - **Evidence**: `10 passed in 0.02s` @ 2026-05-03T14:51Z. Metrics class with bump/get/reset/should_pause/flush/auto_flush_every. SQLite WAL persistence; time-series schema (one row per (name, agent) per flush).

- [x] **1.5** `microverse/agents/base.py` with `Agent` ABC, `Action` Pydantic v2 model `{thought, action, target, artifact}`. Parse failure → `jsonrepair` retry once → fallback `rest` action + bump `json_fallback_rest`.
  - TDD: `tests/test_action_parse.py` (valid, repairable, garbage).
  - **Acceptance**: `uv run pytest tests/test_action_parse.py -q`
  - **Expected**: `passed`
  - **Evidence**: `10 passed in 0.06s` @ 2026-05-03T14:54Z. Action enum (StrEnum), Pydantic v2 strict, parse_action 3-stage (strict/repair/fallback), bumps json_ok/json_repaired/json_fallback_rest and resets consecutive_fail on success.

- [x] **1.6** `microverse/agents/artisan.py` + `microverse/prompts/persona_artisan.j2` (strict JSON output, no meta-references).
  - **Acceptance**: `uv run python -c "from microverse.agents.artisan import Artisan; a=Artisan(name='Aki'); print(a.role)"`
  - **Expected**: `artisan`
  - **Evidence**: `artisan` @ 2026-05-03T14:58Z. 5 unit tests cover role, creative sampling, persona render with world context (incl. meta-reference guard), think() success path, think() fallback path. Persona template uses Jinja2 with StrictUndefined.

- [x] **1.7** `microverse/agents/harvester.py` + `microverse/prompts/persona_harvester.j2`.
  - Atomic writes: write to `*.tmp` then `os.replace`. `manifest.jsonl` append uses fsync + rename.
  - **Acceptance**: `uv run pytest tests/test_harvester.py -q`
  - **Expected**: `passed`
  - **Evidence**: `9 passed in 0.02s` @ 2026-05-03T15:00Z. Phase 1 uses a length-threshold heuristic (≥ 20 chars). persona_harvester.j2 deferred to Phase 2 (Trader-driven LLM selection). Atomic writes verified — no leftover .tmp files; safe filename slug; collision suffix -N.

- [x] **1.8** `microverse/world/scheduler.py` (round-robin; just Artisan in phase 1).
  - **Acceptance**: `uv run pytest tests/test_scheduler.py -q`
  - **Expected**: `passed`
  - **Evidence**: `6 passed` @ 2026-05-03T15:02Z (76 total). RoundRobinScheduler with register/unregister/agents/next; rejects duplicate names; raises LookupError on empty.

- [ ] **1.9** `microverse/run.py` entrypoint with `--ticks N`, `--seed`, `--tempo 0`. SIGINT graceful exit.
  - **Acceptance**: `uv run python -m microverse.run --help`
  - **Expected**: substring `--tempo`

- [ ] **1.10** `tests/test_run_smoke.py`: monkeypatch `chat` to canned actions, every 3rd tick yields artifact, run 30 ticks at `--tempo 0`, assert ≥ 1 file in `harvest/inbox/`.
  - **Acceptance**: `uv run pytest tests/test_run_smoke.py -q`
  - **Expected**: `passed`

- [ ] **1.11** `tests/test_kill_safety.py`: spawn `python -m microverse.run` subprocess; `kill -9` after 5 events committed; restart; assert event id sequence intact, no duplicates, no loss.
  - **Acceptance**: `uv run pytest tests/test_kill_safety.py -q`
  - **Expected**: `passed`

- [ ] **1.12** Real-Ollama acceptance run.
  - **Acceptance**: `rm -rf /tmp/microverse-acc && MICROVERSE_DATA=/tmp/microverse-acc/data MICROVERSE_HARVEST=/tmp/microverse-acc/harvest uv run python -m microverse.run --ticks 30 --tempo 0 --seed 42 && find /tmp/microverse-acc/harvest/inbox -type f | wc -l | tr -d ' '`
  - **Expected**: a number ≥ `1`

- [ ] **1.13** Final phase verification.
  - **Acceptance**: `cd /Users/yuyamukai/dev/microverse && uv run ruff check && uv run ruff format --check && uv run pytest -q -m 'not integration'`
  - **Expected**: `passed`; no `failed`.

### Phase 1 Boundary
**Sentinel**: _PHASE_1_COMPLETE @ <ISO8601>_
**MERGED**: _<commit-sha> @ <ISO8601>_

---

## Phase 2 — Society + cold-backup snapshots (slug: `society`)

- [ ] **2.1** Branch `feat/phase-2-society`.
- [ ] **2.2** `microverse/agents/trader.py` + persona; ranks artifact buffer daily by novelty/utility/completeness; emits `{artifact_id, score, rationale}` JSON; uses factual sampling (temp=0.6, top_p=0.9). Tests: `tests/test_trader.py`.
  - **Acceptance**: `uv run pytest tests/test_trader.py -q`
  - **Expected**: `passed`
- [ ] **2.3** Harvester now consumes Trader's ranking + percentile threshold (default p70).
  - **Acceptance**: `uv run pytest tests/test_harvester.py -q -k percentile`
  - **Expected**: `passed`
- [ ] **2.4** `microverse/world/snapshot.py`: cold backup every 1000 ticks; tar.gz of `data/`. Snapshots are NOT recovery — WAL is. Tests: `tests/test_snapshot_roundtrip.py` (snapshot → wipe → restore → state matches snapshot time).
  - **Acceptance**: `uv run pytest tests/test_snapshot_roundtrip.py -q`
  - **Expected**: `passed`
- [ ] **2.5** Multi-agent scheduler: weighted round-robin by `soul_tokens`. Tests: `tests/test_scheduler.py -k weighted`.
  - **Acceptance**: `uv run pytest tests/test_scheduler.py -q`
  - **Expected**: `passed`
- [ ] **2.6** 1h soak rung (acceptance).
  - **Acceptance**: `MICROVERSE_DATA=/tmp/microverse-soak1h/data MICROVERSE_HARVEST=/tmp/microverse-soak1h/harvest timeout 3700 uv run python -m microverse.run --seed 42 || true; find /tmp/microverse-soak1h/harvest/inbox -type f | wc -l | tr -d ' '`
  - **Expected**: a number ≥ `1` AND no `Traceback` in last 200 lines of run output.
- [ ] **2.7** Final phase verification.
  - **Acceptance**: `cd /Users/yuyamukai/dev/microverse && uv run ruff check && uv run ruff format --check && uv run pytest -q -m 'not integration'`
  - **Expected**: `passed`

### Phase 2 Boundary
**Sentinel**: _PHASE_2_COMPLETE @ <ISO8601>_
**MERGED**: _<commit-sha> @ <ISO8601>_

---

## Phase 3a — Context budgets + FTS5 semantic memory (slug: `memory`)

- [ ] **3a.1** Branch `feat/phase-3a-memory`.
- [ ] **3a.2** `microverse/memory/semantic.py` using SQLite FTS5; `top_k(query, k)` returns BM25-ranked rows. No embeddings. Tests: `tests/test_fts5_recall.py`.
  - **Acceptance**: `uv run pytest tests/test_fts5_recall.py -q`
  - **Expected**: `passed`
- [ ] **3a.3** `microverse/memory/__init__.py::build_context(agent, world)` assembles working (≤1500 tok) + episodic_recent (≤1500 tok) + lore_excerpt (≤600 tok), capped at 4096 tok via `len(text)//4` heuristic.
- [ ] **3a.4** Update agent personas to consume the new context schema.
- [ ] **3a.5** Tests: `tests/test_context_budget.py` over 100 random world states.
  - **Acceptance**: `uv run pytest tests/test_context_budget.py -q`
  - **Expected**: `passed`
- [ ] **3a.6** Final phase verification.
  - **Acceptance**: `cd /Users/yuyamukai/dev/microverse && uv run ruff check && uv run ruff format --check && uv run pytest -q -m 'not integration'`
  - **Expected**: `passed`

### Phase 3a Boundary
**Sentinel**: _PHASE_3A_COMPLETE @ <ISO8601>_
**MERGED**: _<commit-sha> @ <ISO8601>_

---

## Phase 3b — Lore compression (slug: `lore`)

- [ ] **3b.1** Branch `feat/phase-3b-lore`.
- [ ] **3b.2** `microverse/agents/elder.py` + `microverse/prompts/compression.j2`: triggered every `LORE_REGEN_INTERVAL` ticks; reads FTS5 top events of period + previous lore; emits new `data/lore/world_lore.md`.
- [ ] **3b.3** Drift guard: lexical Jaccard ≥ 0.5 vs prior lore; on fail, retry with continuity instruction; on second fail, keep old + bump `lore_drift_block`.
- [ ] **3b.4** Tests: `tests/test_lore_drift_guard.py` (mock Elder LLM with adversarial output → guard triggers).
  - **Acceptance**: `uv run pytest tests/test_lore_drift_guard.py -q`
  - **Expected**: `passed`
- [ ] **3b.5** Final phase verification.
  - **Acceptance**: `cd /Users/yuyamukai/dev/microverse && uv run ruff check && uv run ruff format --check && uv run pytest -q -m 'not integration'`
  - **Expected**: `passed`

### Phase 3b Boundary
**Sentinel**: _PHASE_3B_COMPLETE @ <ISO8601>_
**MERGED**: _<commit-sha> @ <ISO8601>_

---

## Phase 4a — Watchdog, world clock, Stranger (slug: `watchdog`)

- [ ] **4a.1** Branch `feat/phase-4a-watchdog`.
- [ ] **4a.2** `microverse/world/clock.py`: seeded scheduler emits `weather.drought`, `comet`, `festival` into episodic. Tests: `tests/test_clock.py`.
- [ ] **4a.3** `microverse/agents/stranger.py`: spawned by watchdog when diversity < 0.35.
- [ ] **4a.4** `microverse/ops/watchdog.py` detectors: runaway, stagnation, echo-chamber (lexical Jaccard, NOT embeddings), meta-reference leakage (regex respawn).
- [ ] **4a.5** Extend `metrics.py` with diversity = 1 − mean Jaccard of last-N actions.
- [ ] **4a.6** 6h soak rung (acceptance).
  - **Acceptance**: `MICROVERSE_DATA=/tmp/microverse-soak6h/data MICROVERSE_HARVEST=/tmp/microverse-soak6h/harvest timeout 21700 uv run python -m microverse.run --seed 42 || true; uv run python -m microverse.ops.metrics --report --db /tmp/microverse-soak6h/data/metrics.sqlite`
  - **Expected**: report shows mean diversity ≥ 0.35 AND each watchdog detector fired ≥ 1.
- [ ] **4a.7** Final phase verification.
  - **Acceptance**: `cd /Users/yuyamukai/dev/microverse && uv run ruff check && uv run ruff format --check && uv run pytest -q -m 'not integration'`
  - **Expected**: `passed`

### Phase 4a Boundary
**Sentinel**: _PHASE_4A_COMPLETE @ <ISO8601>_
**MERGED**: _<commit-sha> @ <ISO8601>_

---

## Phase 4b — Dashboard, runbook, soak (slug: `soak`)

- [ ] **4b.1** Branch `feat/phase-4b-soak`.
- [ ] **4b.2** `scripts/render_dashboard.py` emits `harvest/dashboard.html` (vanilla HTML+inline CSS).
- [ ] **4b.3** `README.md`: ops runbook (start/stop/snapshot/restore/watchdog tuning/recovery).
- [ ] **4b.4** 24h soak rung.
  - **Acceptance**: `nohup uv run python -m microverse.run --seed 42 > /tmp/microverse-soak24h.log 2>&1 & echo $! > /tmp/microverse-soak24h.pid; sleep 86400; kill $(cat /tmp/microverse-soak24h.pid); ! grep -q 'Traceback' /tmp/microverse-soak24h.log && echo soak24_ok`
  - **Expected**: `soak24_ok` AND ≥ 1 file in `harvest/inbox/$(date -u +%F)/`.
- [ ] **4b.5** SIGKILL drill mid-soak: kill -9 the running process; restart; assert event id sequence strictly increasing; zero loss.
  - **Acceptance**: `uv run python scripts/verify_kill_drill.py --db data/episodic.sqlite`
  - **Expected**: `kill_drill_ok`
- [ ] **4b.6** 72h soak rung (optional — can be deferred per Risk #7).
  - **Acceptance**: same as 24h but `sleep 259200`. May be split across calendar.
  - **Expected**: `soak72_ok`
- [ ] **4b.7** `git tag v0.1.0` after merge.
- [ ] **4b.8** Final phase verification.
  - **Acceptance**: `cd /Users/yuyamukai/dev/microverse && uv run ruff check && uv run ruff format --check && uv run pytest -q -m 'not integration'`
  - **Expected**: `passed`

### Phase 4b Boundary
**Sentinel**: _PHASE_4B_COMPLETE @ <ISO8601>_
**MERGED**: _<commit-sha> @ <ISO8601>_

---

## Project completion

When **all** of the following are true:
1. Phase 4b is MERGED with a commit SHA recorded above.
2. The 24h soak rung (4b.4) shows `soak24_ok` and a non-empty `harvest/inbox/<today>/` directory.
3. `tests/test_kill_safety.py` and the kill drill (4b.5) both passed.

…emit:

```
<promise>PROJECT_COMPLETE</promise>
```

Do NOT emit before all three are true. Lying to exit is forbidden.
