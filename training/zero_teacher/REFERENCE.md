# quoridor-zero.ink scrape reference

Scraped: 2026-06-17 from [https://quoridor-zero.ink](https://quoridor-zero.ink)

**Agent handoff:** [`HANDOFF.md`](HANDOFF.md) (zero-ink) · [`../ARCHITECTURE_HANDOFF.md`](../ARCHITECTURE_HANDOFF.md) (master architecture)

Local mirror: `training/data/zero_teacher/scrape/`
Refresh: `python -m training.zero_teacher.scrape_site`

## What this site is

**Quoridor CPU Analysis** — a Lit web UI over a server-side **AlphaZero ResNet + MCTS**
engine (checkpoint `resume-188/model_000159`, 96 filters, 10 blocks, iteration 159).

It is **not** a Titanium/HalfPW engine. Value is win-probability in `[-1, +1]`.
Search is PUCT MCTS with configurable visits (default 400, UI allows up to 5M).

### Safe training use (recommended)

| Use                                     | OK?     | Notes                                            |
| --------------------------------------- | ------- | ------------------------------------------------ |
| Main HalfPW WDL `train.py` eval distill | **No**  | Different net family and value scale             |
| Single-position Ka-style teacher cp     | **No**  | Same failure mode you disabled                   |
| **Search-budget / attention sidecar**   | **Yes** | `visitFraction`, `prior`, `q`, continuous stream |
| Full-game opponent (WDL only)           | Maybe   | `/api/bot-move` automatable; strength TBD        |
| UI layout clone                         | **Yes** | CSS variables + component tree below             |

## UI layout (steal this)

Single-page app. Root: `<server-app-root>` (Lit).

```
.app-shell
├── .app-header
│   ├── eyebrow "Quoridor CPU"
│   ├── h1 "Server analysis"
│   └── .header-meta
│       ├── .status-pill   (turn / status text)
│       └── .model-pill    (model id)
├── .banner (+ .banner-dot, .error variant)
└── .workspace                    [grid: board | 430px side panel]
    ├── .board-column
    │   └── .board-card
    │       ├── .board-toolbar (h2 Board, .wall-summary P0/P1 counts)
    │       └── .board-center
    │           └── quoridor-board
    │               ├── 9×9 .cell buttons
    │               ├── .pawn.p0 / .pawn.p1
    │               ├── .wall.horizontal / .wall.vertical
    │               └── .wall-slot (placement ghosts)
    └── .side-panel
        ├── .card.eval-card
        │   ├── h2 Evaluation
        │   ├── .evalbar + .marker (red ← → green bar)
        │   └── source label: raw value | search root | live search | bot
        ├── .card.controls-card
        │   ├── Mode: Analyze | Play vs bot
        │   ├── Position: Undo | Redo
        │   └── Advanced: visits slider, batchSize, cpuct, threads
        └── analysis-panel
            ├── tabs: Policy | Search
            ├── Policy tab: Value, entropy, move prior table
            └── Search tab: Root value, total visits, depth, PV, move table
                columns: Move | Prior | Visits | Visit% | Q
```

### Design tokens (from `index-DMDGc9lg.css`)

```css
--bg: #f4f7fb;
--surface: #ffffff;
--accent: #2563eb;
--p0: #e85d5d; /* red pawn */
--p1: #3b76f6; /* blue pawn */
--wall: #566274;
--legal: #0f8b78;
--good: #0f8f66;
--bad: #ca3f3f;
--highlight: #f2bd3d;
--cell: clamp(28px, 5.2vw, 58px);
```

Responsive: stacks to single column below 1060px.

## REST API

Base: `https://quoridor-zero.ink`

| Method | Path                       | Purpose                                            |
| ------ | -------------------------- | -------------------------------------------------- |
| GET    | `/api/models`              | List checkpoints + default MCTS settings           |
| POST   | `/api/position`            | Legal moves + enriched snapshot from compact state |
| POST   | `/api/analysis/policy`     | Raw net value + policy priors (no MCTS)            |
| POST   | `/api/analysis/search`     | One-shot MCTS to `settings.visits`                 |
| POST   | `/api/analysis/continuous` | **NDJSON stream** of deepening search snapshots    |
| POST   | `/api/bot-move`            | Best move + `stateAfter` + think time              |

### Compact state (all POST bodies)

```json
{
  "currentPlayer": 0,
  "player0Cell": 36,
  "player1Cell": 76,
  "player0Walls": 10,
  "player1Walls": 10,
  "horizontalWalls": [{ "x": 3, "y": 5 }],
  "verticalWalls": [{ "x": 4, "y": 2 }]
}
```

**Coordinates:** `playerNCell = row * 9 + col` where **row 0 = bottom**, col 0 = left.
Start: P0 bottom center `(4,0)→36`, P1 top center `(4,8)→76`.

Walls: `horizontal` / `vertical` at slot top-left `(x,y)`.

### MCTS settings

```json
{
  "visits": 400,
  "batchSize": 16,
  "cpuct": 2.5,
  "threads": 2
}
```

Defaults from `/api/models`: visits=400, batchSize=16, cpuct=2.5, threads=4.
UI default for manual search: visits=10000, max 5_000_000.

### Policy response (`/api/analysis/policy`)

```json
{
  "value": -0.453,
  "entropy": 2.768,
  "moves": [
    {
      "move": { "kind": "pawn", "target": 37, "x": -1, "y": -1 },
      "action": 37,
      "prior": 0.412,
      "visits": 0,
      "visitFraction": 0,
      "q": 0
    }
  ]
}
```

- `value`: raw net win prob for **side to move** in `[-1,1]`
- UI flips sign when displaying for P1 (`display = stm==1 ? -value : value`)

### Search response (`/api/analysis/search`)

```json
{
  "rootValue": 0.427,
  "totalVisits": 400,
  "rootChildVisits": 399,
  "moves": [
    {
      "move": { "kind": "pawn", "target": 37 },
      "action": 37,
      "prior": 0.412,
      "visits": 322,
      "visitFraction": 0.807,
      "q": 0.507
    }
  ]
}
```

**Training gold fields (search-budget sidecar):**

| Field                      | Meaning for Titanium                               |
| -------------------------- | -------------------------------------------------- |
| `visitFraction`            | MCTS attention share — which child deserves budget |
| `prior` vs `visitFraction` | Expansion signal (search overrating vs net prior)  |
| `q`                        | MCTS line value after visits                       |
| `visits`                   | Absolute child count at root                       |
| `rootValue`                | Converged win estimate after search                |

### Continuous stream (`/api/analysis/continuous`)

Newline-delimited JSON. Each chunk is a full search snapshot while MCTS runs:

```json
{
  "rootValue": 0.822,
  "totalVisits": 662,
  "rootChildVisits": 661,
  "depth": 12,
  "pv": [{ "kind": "pawn", "target": 30 }, "..."],
  "moves": ["... same shape as search ..."]
}
```

Observed progression on one midgame: visits 0 → 73 → 153 → 246 → … → 1045.
Use last chunk or delta between chunks as **depth-budget convergence** label.

### Bot move (`/api/bot-move`)

```json
{
  "move": {"kind": "pawn", "target": 37},
  "score": 0.437,
  "thinkMs": 2097,
  "stateAfter": { "... full position snapshot ..." }
}
```

## ACE / Titanium move bridge

Our DB uses ACE algebraic (`e2`, `d6h`, `e4v`). Convert to zero state:

```python
from training.zero_teacher.client import ace_moves_to_zero_state
state = ace_moves_to_zero_state(["e2", "e8", "e3"])  # replay from start
```

Pawn ACE cell `r,c = divmod(ace,9)` (ACE row 0 = top) → zero `target = (8-r)*9+c`.
Wall ACE `100+slot` → horizontal `{x:c, y:7-r}`; `200+slot` → vertical same.

Validate with `client.position(state)` before labeling.

## Training pipeline hookup

**Do not** feed `value`/`q` into HalfPW WDL loss.

For the **search-pressure sidecar** (same family as `collect_search_importance.py`):

```powershell
# Scrape refresh (optional)
python -m training.zero_teacher.scrape_site

# Collect labels from DB game prefixes
python -m training.zero_teacher.collect_budget --from-db --limit 100 --visits 400

# Fast rollout pass (50 visits)
python -m training.zero_teacher.collect_budget --from-db --limit 200 --visits 50

# Or self-generate positions by playing their bot
python -m training.zero_teacher.collect_budget --limit 30 --bot-plies 40 --visits 400

# Train sidecar (reuses frozen HalfPW trunk)
python training/train_search_importance.py --data training/data/zero_teacher/labels/search_budget.jsonl
```

Output schema: `zero-search-budget-v1` with `search_pressure` in `[-1,+1]`.

Mapping logic (`search_pressure_from_zero`):

- High `top_visit_fraction` → search already concentrated → lower extra budget
- Large `prior_visit_gap` (visit% >> prior) → search discovered important line → higher budget
- High `visit_entropy` → many competing lines → higher budget

## Scraped files

| File                 | Contents                                                                |
| -------------------- | ----------------------------------------------------------------------- |
| `index.html`         | SPA shell                                                               |
| `index-DxhKgA6c.js`  | Lit components + API client                                             |
| `index-DMDGc9lg.css` | Full design system                                                      |
| `api_samples.json`   | Live responses: start/mid, policy, search, bot plies, continuous chunks |
| `meta.json`          | Scrape metadata                                                         |

## Client code

- `training/zero_teacher/client.py` — typed API wrapper
- `training/zero_teacher/collect_budget.py` — JSONL collector
- `training/zero_teacher/scrape_site.py` — refresh local mirror

## Rate / ops notes

- ~2–4s per `/api/bot-move` at 400 visits
- `/api/analysis/continuous` holds connection until search finishes
- External dependency: site must stay up; cache JSONL locally
- Be polite: small batches, don't hammer with 5M visit requests
