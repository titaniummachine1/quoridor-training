# Handoff — quoridor-zero.ink teacher (2026-06-17)

**Master architecture doc:** [`../ARCHITECTURE_HANDOFF.md`](../ARCHITECTURE_HANDOFF.md) — HalfPW dual-head philosophy, training phases, search integration rules.

Read this file if you are picking up work on **external MCTS label mining** only.

## What we did

1. **Scraped** [quoridor-zero.ink](https://quoridor-zero.ink): SPA assets (`index.html`, bundled JS/CSS) plus live API samples (policy, search, bot, continuous NDJSON stream).
2. **Organized** everything under `training/zero_teacher/` (code) and `training/data/zero_teacher/` (data). Removed old root scripts (`scrape_quoridor_zero.py`, `zero_teacher_client.py`, `collect_zero_search_budget.py`) and the duplicate `training/data/quoridor_zero_scrape/` tree.
3. **Built a label pipeline** that replays ACE positions (from DB or bot self-play), calls zero-ink MCTS at 50–400 visits, and writes JSONL with per-move attention tables + a collapsed `search_pressure` scalar.
4. **Smoke-tested** the collector against the live API (4 rows at 50 visits).
5. **Documented** API/UI in `REFERENCE.md`; wired a short pointer in `training/README.md`.

Ka teacher distillation remains **disabled** (fail-closed stubs). Main HalfPW `train.py` is **unchanged** (game WDL only).

## What is new

| Path                                      | Role                                                                                      |
| ----------------------------------------- | ----------------------------------------------------------------------------------------- |
| `training/zero_teacher/client.py`         | HTTP client, ACE↔zero state bridge, `search_budget_features`, `search_pressure_from_zero` |
| `training/zero_teacher/collect_budget.py` | Label collector (`python -m training.zero_teacher.collect_budget`)                        |
| `training/zero_teacher/scrape_site.py`    | Refresh local mirror                                                                      |
| `training/zero_teacher/paths.py`          | `SCRAPE_DIR`, `LABELS_DIR`, `DEFAULT_LABELS`                                              |
| `training/data/zero_teacher/scrape/`      | Site mirror + `api_samples.json` + `meta.json` (bulk gitignored)                          |
| `training/data/zero_teacher/labels/`      | JSONL output (gitignored)                                                                 |
| `training/train_search_importance.py`     | Existing sidecar trainer — accepts zero labels via `search_pressure` field                |

### Label schema (`zero-search-budget-v1`)

Each JSONL row has:

- `moves` / `moves_bin` — ACE prefix (replays through `eval-batch` at train time)
- `search.top_moves[]` — **gold attention**: `visit_fraction`, `prior`, `q`, `visits` per move
- `stream_last` — last chunk from `/api/analysis/continuous` (depth, PV, visit growth)
- `search_pressure` — single scalar in `[-1,+1]` derived from concentration / prior gap / entropy

The scalar is what `train_search_importance.py` trains today. The full `top_moves` table is stored but **not yet used in the loss**.

## Why this teacher vs Ka

Ka never disclosed search internals. Zero-ink exposes what Ka could not:

| Signal                     | Ka     | zero-ink                   |
| -------------------------- | ------ | -------------------------- |
| MCTS visit share per move  | No     | `visitFraction`            |
| Net prior before search    | No     | `/api/analysis/policy`     |
| Line Q after rollouts      | Opaque | `q` per child              |
| Search deepening over time | No     | `/api/analysis/continuous` |

**Do not** distill raw `value` / `q` into HalfPW WDL — different net family, same failure mode as lightweight Ka eval.

**Do** mine search-budget / attention signals for search control experiments.

## What you should probably start doing

Low-risk order. No engine changes until labels look sane.

### 1. Collect a small mining batch

```powershell
# Fast probe (~2–4s/position at 400 visits)
python -m training.zero_teacher.collect_budget --from-db --limit 200 --visits 50 --out training/data/zero_teacher/labels/search_budget_50.jsonl

# Higher quality pass
python -m training.zero_teacher.collect_budget --from-db --limit 500 --visits 400 --out training/data/zero_teacher/labels/search_budget.jsonl
```

- Cache JSONL locally — site is an external dependency.
- Be polite: don't hammer 5M-visit requests; 50–400 is enough for attention shape.
- `--from-db` samples random prefixes from `all_games.db`; dedupes on `moves_bin`.

### 2. Inspect before training

Check label quality before any big train run:

- Distribution of `search_pressure` (should span `[-1,+1]`, not collapse to one value)
- `top_visit_fraction` vs `prior` gaps — cases where MCTS strongly overrides the net
- `visit_entropy` — spread vs concentrated roots
- Replay a few `moves` prefixes in the zero-ink UI to sanity-check ACE bridge

### 3. Train the existing scalar sidecar

```powershell
python training/train_search_importance.py --data training/data/zero_teacher/labels/search_budget.jsonl --cpu
```

Success criterion already in the script: **validation MSE beats constant-mean baseline**. If it doesn't beat baseline, more data or better targets before engine work.

### 4. Compare against titanium-native labels (optional)

```powershell
python training/collect_search_importance.py --limit 200
python training/train_search_importance.py --data training/data/search_pressure.jsonl
```

Compare which teacher gives a lower val loss / more useful examples. Zero-ink = external MCTS attention; titanium path = shallow-vs-deep on our own search.

### 5. Later (only if scalar sidecar validates)

Not started. Documented here so the next agent doesn't reinvent:

1. **Per-move policy distill** — use stored `top_moves[].visit_fraction` as softmax targets over legal moves (move ordering), not just one scalar.
2. **Engine export** — sidecar weights into Rust; hook LMR/extension/move-order by at most ±1 ply with mate/TT overrides.
3. **A/B matches** — before any default-on in live search.

`train_search_importance.py` explicitly says the head is **not exported to the engine yet**. Nothing in `engine/` references `search_pressure`.

## What not to do

- Feed zero-ink `value` / `q` into main `train.py` WDL loss
- Re-enable Ka single-position eval distill
- Wire sidecar into live search without beating constant baseline + A/B
- Commit bulk scrape/labels (gitignored under `training/data/zero_teacher/`)

## Quick reference

| Doc                                                        | Contents                                 |
| ---------------------------------------------------------- | ---------------------------------------- |
| [`../ARCHITECTURE_HANDOFF.md`](../ARCHITECTURE_HANDOFF.md) | Master NN + minimax architecture handoff |
| `README.md`                                                | Layout + commands                        |
| `REFERENCE.md`                                             | Full API, UI tokens, coordinate bridge   |
| `HANDOFF.md`                                               | This file (zero-ink teacher only)        |

Scrape location: `training/data/zero_teacher/scrape/`  
Default labels output: `training/data/zero_teacher/labels/search_budget.jsonl`
