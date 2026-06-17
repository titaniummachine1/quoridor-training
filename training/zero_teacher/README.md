# Zero teacher — MCTS attention distillation

Distill **search work distribution** from [quoridor-zero.ink](https://quoridor-zero.ink) into our
search-pressure sidecar. Small MCTS rollouts (50–400 visits), not per-node eval, not main WDL.

**Docs:** [`../ARCHITECTURE_HANDOFF.md`](../ARCHITECTURE_HANDOFF.md) (master) · [`HANDOFF.md`](HANDOFF.md) (zero-ink steps)

## What we train

| Signal from zero-ink       | Our use                            |
| -------------------------- | ---------------------------------- |
| `visitFraction` per move   | Which child deserves search budget |
| `prior` vs `visitFraction` | Net underrating → expand this line |
| `continuous` stream        | Budget convergence / depth         |
| `search_pressure` scalar   | Sidecar target in `[-1,+1]`        |

Main HalfPW `train.py` is **unchanged**. Ka-style eval distill stays **disabled**.

## Layout

```text
training/zero_teacher/
  README.md           this file
  HANDOFF.md          agent handoff — what we did, what to do next
  REFERENCE.md        full API + UI scrape spec
  client.py           HTTP API + ACE bridge
  collect_budget.py   label collector
  scrape_site.py      refresh local mirror
  paths.py            data dir constants

training/data/zero_teacher/
  scrape/             site mirror (html/js/css/api_samples — gitignored bulk)
  labels/             search_budget.jsonl output (gitignored)
```

## Commands

```powershell
# Refresh scrape (optional)
python -m training.zero_teacher.scrape_site

# Collect labels — fast pass
python -m training.zero_teacher.collect_budget --from-db --limit 100 --visits 50

# Collect labels — default quality
python -m training.zero_teacher.collect_budget --from-db --limit 100 --visits 400

# Train sidecar (frozen HalfPW trunk)
python training/train_search_importance.py --data training/data/zero_teacher/labels/search_budget.jsonl
```

Full API schemas and UI clone notes: `REFERENCE.md`.
