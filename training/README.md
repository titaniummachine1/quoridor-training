# HalfPW NNUE retrain — pipeline

Augment the existing HalfPW net (gen13 ACE) with explicit geometry inputs so it
stops approximating shortest-path/tempo through hidden layers and spends capacity
on tactics. **Fine-tune the existing weights — do not retrain from scratch.**

## Why augment, not retrain

The current weights already encode tactical knowledge. A full retrain discards
it. We add new input planes (zero-initialised) and fine-tune, so learning is
correcting residuals, not relearning geometry.

## Frozen feature set (the training gate)

Soft net **inputs**, not hard rules — the certificate/race-proof layer is the only
hard override (handles every "...unless it forces a win"). Material is not counted;
the engine races on tempo.

| Feature                                             | Status                            |
| --------------------------------------------------- | --------------------------------- |
| tempo (`d_opp − d_me`)                              | already input (`ws[1]`)           |
| per-player distance                                 | already input (`ws[3]`, `ws[4]`)  |
| wall-diff                                           | already input (`ws[2]`, `ws[10]`) |
| tempo × opponent-wall-count (fragile lead)          | NEW skip slot                     |
| per-player bottleneck / route-flex count (≥2-paths) | existing extractor → input        |
| distance-from-player field (sparse, per cell)       | wired (`wp`, player BFS)          |
| distance-from-path / delta field (sparse, per cell) | wired (`wd`, goal dist − pawn)    |

The two per-cell fields are kept **separate** (not the pre-combined CAT heat): the
net learns the interaction; pre-mixing forces it to re-separate. Walls are priced
by the BFS distance field exactly (incl. chaining), so we never hand-code a
per-wall tempo value. All features ride one flood per eval node.

## Pipeline

- `halfpw.py` — Python port of the engine forward pass (walls-present net path).
- `parity_check.py` — verifies `halfpw.forward` == `titanium eval … --json`
  bit-for-bit. **Must pass before any training.**
- `nnue_guards.py` — artifact caps (500MB warn / 1GB hard), Elo-drop snapshots,
  pre-train sanity (v15 vs ti-pure win rate), checkpoint pruning.
- `run_nnue_cycle.py` — one guarded training epoch from `all_games.db`.
- `run_swiss_overnight.py` — **one program**: game pool + background NNUE
  (disable with `--no-train`).

### Architecture status

Engine `eval --json` and `train.py` share the same HalfPW forward including
**wf/wp/wd** per-cell field planes (zero-init). Run `extend_field_planes.py`
once, then `parity_check.py` before training.

### Overnight + training guards

| Guard              | Behavior                                                      |
| ------------------ | ------------------------------------------------------------- |
| New games          | train after ≥32 games since last epoch                        |
| Interval           | min 10 min between epochs                                     |
| Artifact soft cap  | 500 MB checkpoints → prune old ckpt_step\*                    |
| Artifact hard cap  | 1 GB → refuse train                                           |
| Pre-train          | skip if v15 vs ti-pure >58% (already crushing baseline)       |
| Elo drop           | snapshot to `checkpoints/snapshots/` if ladder −12+ from peak |
| Pre-epoch snapshot | always `pre_train_epoch` before each run                      |

- (next) feature extraction: layered flood → dist-from-player + delta planes
  (sparse over corridor cells); wire into the engine `evaluate()` and the Python
  model together, measuring NPS.
- (next) data generation: self-play + mixed Ka/Ishtar games dumped as feature
  records (same JSON as `eval --json`) + search-derived target value.
- (next) train: fine-tune from `engine/baseline/net_weights.baseline.bin`,
  KataGo-style auxiliary heads (distance/wall-ownership) as training-only
  regularisers; curriculum: imitation (mixed teachers) → stop before collapse →
  self-play.
- measure: new-weights engine vs `engine/baseline/titanium_baseline.exe`. Adopt
  only on a clear win.

## Engine commands

- `titanium eval <moves>` — net eval for a position.
- `titanium eval <moves> --json` — raw inputs + eval (parity + training-data format).
- `titanium match --a <eng> --b <eng> --games N --time S` — self-play strength.

## Baseline (pre-retrain, 2026-06-15)

grafted vs plain ace-v13 @ 2s: 54–58 / 112 = 48.2% (±9.3%) ≈ −12 Elo (within
noise; even). This is the number the retrained net must beat.
