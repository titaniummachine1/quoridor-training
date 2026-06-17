# HalfPW NNUE retrain - pipeline

Fine-tune the existing gen13 ACE HalfPW net. Do not retrain from scratch: tactical
knowledge is already in the weights; new inputs are zero-init and learned as residuals.

## Frozen Architecture (2026-06)

| Component         | Contract                                                                                  |
| ----------------- | ----------------------------------------------------------------------------------------- |
| Field planes (11) | goal_inv, pawn_fwd, corridor_delta, path_cross, choke x2, contested per-player BFS        |
| Sparse embeds     | w1c (128 wall slots), po, px                                                              |
| ws[0-12]          | trained interaction terms; do not change semantics                                        |
| ws[13]            | fragile-lead formula (`pd * w_opp / 10`)                                                  |
| ws[14]            | `legal_wall_count / 128` (path-valid wall slots)                                          |
| ws[15]            | opponent corridor width                                                                   |
| Search            | titanium-v15: full legal movegen, warm session, not routed through infinite `session_v15` |

Certificate/race-proof layer remains the only hard eval override.

## Data Pipeline

SQLite stores compact move sequences and the final winner. The normal path is:

```text
all_games.db (moves_bin + outcome)
    -> expand_games() / eval-batch  (feature authority)
    -> train.py                     (WDL loss on materialized records)
```

Every valid completed game is training data. Ka/JS/frozen/self-play games are all
trainable if the move list replays and the winner is known. Quoridor has no draw target.
Single-position Ka/CNN labels are not part of the default pipeline.

If `eval-batch` is correct, training is correct. There is no hidden dataset drift on the
`.db` path. Hard-fail if `legal_wall_count` is missing. Checkpoints must carry schema
`halfpw-field11-ws14-legal-wall-v1` to resume.

## Scripts

| Script                       | Role                                                                     |
| ---------------------------- | ------------------------------------------------------------------------ |
| `halfpw.py`                  | Python port of engine forward pass                                       |
| `parity_check.py`            | `halfpw.forward` == `titanium eval ... --json` before train              |
| `engine_identity.py`         | SHA256 stamp for the single validated `titanium.exe`                     |
| `regression_triage.py`       | Classify strength drops: eval / search / rollout before blaming training |
| `nnue_guards.py`             | Artifact caps, Elo snapshots, pre-train gates, deploy                    |
| `datagen.py`                 | Game ingest + `eval-batch` expansion                                     |
| `run_nnue_cycle.py`          | Guarded micro/batch train from `all_games.db`                            |
| `run_swiss_overnight.py`     | Game pool + background NNUE (`--no-train` to disable)                    |
| `collect_search_importance.py` | Build shallow-vs-deep search-pressure labels for scalar experiments |
| `train_search_importance.py` | Train the sidecar search-pressure head                                |
| `run_search_pressure_experiment.py` | Cloud/overnight wrapper for pressure-label collection + head training |
| `probe_legal_wall_signal.py` | Correlation probe for ws[14] ablation                                    |
| `plateau_probe.py`           | Eval-drift / promotion gate                                              |

## Pre-Flight

Native build only (`RUSTFLAGS="-C target-cpu=native"`):

```powershell
cd engine
$env:RUSTFLAGS="-C target-cpu=native"
cargo build --release
cd ..
python training/engine_identity.py --write
python training/parity_check.py
python training/regression_triage.py
```

Restart the overnight pool after rebuild so match slots and eval-batch share the same binary.

## Overnight + Training Guards

| Guard              | Behavior                                                       |
| ------------------ | -------------------------------------------------------------- |
| New games          | train after >=32 games since last epoch                        |
| Interval           | min 10 min between epochs                                      |
| Artifact soft cap  | 500 MB checkpoints -> prune old `ckpt_step*`                   |
| Artifact hard cap  | 1 GB -> refuse train                                           |
| Pre-train win rate | skip batch if v15 vs ti-pure >70%; warn micro at >58%          |
| Elo drop           | snapshot to `checkpoints/snapshots/` if ladder -12+ from peak  |
| Resume schema      | refuse checkpoints without `halfpw-field11-ws14-legal-wall-v1` |
| Engine stamp       | block eval/self-play/train if `titanium.exe` hash changed      |
| Parity/schema      | training blocked unless parity passes and `legal_wall_count` exists |

Before search architecture experiments (`session_v15`, ponder, infinite search), run
`engine_identity.py --write` and `regression_triage.py`. Do not assume a training problem
until eval/search/rollout smokes pass.

## Engine Commands

- `titanium eval <moves> --json` - raw inputs + eval for parity and training format
- `titanium eval-batch` - stdin: one move sequence per line; JSON per position
- `titanium match --a <eng> --b <eng> --games N --time S` - self-play strength

## Search-Pressure Labels

The leaf-local search scalar is collected as a sidecar dataset first:

```powershell
python training/run_search_pressure_experiment.py --labels 2000 --chunk 200 --time 2.0 --cpu
```

For a longer cloud run:

```powershell
python training/run_search_pressure_experiment.py --labels 20000 --chunk 500 --time 2.0 --epochs 50
```

The target is shallow-vs-deep search pressure. Read it as the parent node asking about the
child it just reached: how much do we trust this shallow evaluation, and does this node
deserve less or more budget than normal? `-1` means already saturated, `0` means normal,
`+1` means shallow search is likely unstable and this child deserves more budget. The
trained sidecar is not wired into live reductions until its labels and validation show it
helps rather than making search weaker.

Safe activation order:

1. Collect labels only; inspect distribution and examples.
2. Train the sidecar; require validation loss below a constant-mean baseline.
3. Add engine export/inference only as diagnostics.
4. Let pressure change LMR/extension by at most one ply, with mate/TT/forced-move overrides.
5. Run A/B matches before making it default.

## External AlphaZero Data

`KaAiData/ANOTHER TRAINING DAT ASTUFF SUPER USEFULL` contains per-position MCTS samples:
board state, sparse policy, side-to-move outcome, and root value. It is useful, but it is
not replayable game history, so it must not be imported into `all_games.db`.

Use it later as a separate streaming source for diagnostics or optional root-value/policy
experiments. Filter cutoff draws instead of forcing them into Quoridor WDL labels.

## Baseline (pre-retrain, 2026-06-15)

Grafted vs plain ace-v13 @ 2s: 54-58 / 112 = 48.2% (+/-9.3%) ~= -12 Elo (within noise).
