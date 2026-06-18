#!/usr/bin/env python3
"""Train a sidecar leaf search-pressure head from shallow-vs-deep labels.

The head reuses frozen HalfPW feature construction. The JSONL contains only
move prefixes and labels; this script regenerates features through eval-batch
at training time so the sidecar does not balloon into stored position tensors.
It is intentionally not exported into the Rust engine yet; use it to prove that
a leaf-local scalar can predict whether a child is already searched enough or
deserves more budget than the engine would normally spend.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "training"))

from train import HalfPW, NET_H, QuoridorDataset, WEIGHTS  # noqa: E402
from datagen import eval_batch  # noqa: E402
from move_codec import unpack_moves  # noqa: E402


def row_moves(row: dict) -> list[str]:
    if row.get("moves_bin"):
        return unpack_moves(base64.b64decode(row["moves_bin"]))
    return list(row.get("moves", []))


def row_target(row: dict) -> float:
    target = row.get("search_pressure")
    if target is None:
        target = row.get("depth_scalar")
    if target is None:
        target = 2.0 * float(row["importance"]) - 1.0
    return float(target)


def row_source(row: dict) -> str:
    teacher = str(row.get("teacher") or "titanium-native")
    return "zero" if "zero" in teacher else "native"


def grouped_split(rows: list[dict], seed: int, val_fraction: float = 0.10) -> tuple[list[dict], list[dict]]:
    """Split whole source games, stratified by teacher family."""
    rng = random.Random(seed)
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        key = str(row.get("source_game_key") or row.get("moves_bin") or "")
        grouped[(row_source(row), key)].append(row)
    by_source: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key in grouped:
        by_source[key[0]].append(key)
    val_keys: set[tuple[str, str]] = set()
    for keys in by_source.values():
        rng.shuffle(keys)
        n_val = max(1, round(len(keys) * val_fraction)) if len(keys) > 1 else 0
        val_keys.update(keys[:n_val])
    train = [row for key, batch in grouped.items() if key not in val_keys for row in batch]
    val = [row for key, batch in grouped.items() if key in val_keys for row in batch]
    return train, val


class ImportanceDataset(Dataset):
    def __init__(self, rows: list[dict]):
        self.rows = rows
        features = []
        for rec, row in zip(eval_batch([row_moves(r) for r in rows]), rows):
            rec["outcome"] = row.get("outcome", 1)
            features.append(rec)
        self.base = QuoridorDataset(features)

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, idx):
        item = self.base[idx]
        item["search_pressure"] = torch.tensor(row_target(self.rows[idx]), dtype=torch.float32)
        return item


class ImportanceHead(nn.Module):
    def __init__(self, weights_path: Path):
        super().__init__()
        self.trunk = HalfPW(weights_path)
        for p in self.trunk.parameters():
            p.requires_grad = False
        # One tiny leaf-local actuator head: this is not a policy over moves,
        # only a trust/budget signal for the child node already reached.
        self.head = nn.Linear(NET_H, 1)

    def forward(self, batch):
        with torch.no_grad():
            hid = self.trunk.hidden_features(batch)
        return torch.tanh(self.head(hid)).squeeze(-1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", action="append", default=None,
                    help="JSONL input; repeat to combine native and zero labels")
    ap.add_argument("--weights", default=str(WEIGHTS))
    ap.add_argument("--out", default="training/checkpoints/search_pressure_head.pt")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--cpu", action="store_true")
    args = ap.parse_args()

    data_paths = args.data or ["training/data/search_pressure.jsonl"]
    rows = []
    for data_path in data_paths:
        rows.extend(
            json.loads(line)
            for line in Path(data_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if len(rows) < 8:
        print(f"need at least 8 rows, got {len(rows)}")
        return 1
    train_rows, val_rows = grouped_split(rows, args.seed)
    if not val_rows:
        print("need labels from at least two source games for grouped validation")
        return 1
    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")

    targets = [row_target(row) for row in rows]
    train_mean = sum(row_target(row) for row in train_rows) / max(1, len(train_rows))
    val_targets = [row_target(row) for row in val_rows]
    baseline_val = (
        sum((t - train_mean) ** 2 for t in val_targets) / len(val_targets)
        if val_targets
        else float("inf")
    )
    print(
        f"rows={len(rows)} train={len(train_rows)} val={len(val_rows)} "
        f"target_mean={sum(targets)/len(targets):+.3f} "
        f"target_min={min(targets):+.3f} target_max={max(targets):+.3f} "
        f"const_baseline_val={baseline_val:.5f}"
    )

    train_means = {}
    for source in {row_source(r) for r in train_rows}:
        source_targets = [row_target(r) for r in train_rows if row_source(r) == source]
        train_means[source] = sum(source_targets) / len(source_targets)
    source_baselines = {}
    for source in {row_source(r) for r in val_rows}:
        source_targets = [row_target(r) for r in val_rows if row_source(r) == source]
        mean = train_means.get(source, train_mean)
        source_baselines[source] = sum((v - mean) ** 2 for v in source_targets) / len(source_targets)

    model = ImportanceHead(Path(args.weights)).to(device)
    opt = torch.optim.Adam(model.head.parameters(), lr=args.lr)
    train_dl = DataLoader(ImportanceDataset(train_rows), batch_size=args.batch, shuffle=True)
    val_dl = DataLoader(ImportanceDataset(val_rows), batch_size=args.batch)

    def to_device(batch):
        return {k: v.to(device) for k, v in batch.items()}

    def run_val(details: bool = False):
        model.eval()
        total, n = 0.0, 0
        predictions = []
        with torch.no_grad():
            for batch in val_dl:
                batch = to_device(batch)
                pred = model(batch)
                loss = F.mse_loss(pred, batch["search_pressure"])
                total += loss.item() * len(pred)
                n += len(pred)
                if details:
                    predictions.extend(pred.detach().cpu().tolist())
        model.train()
        return (total / max(1, n), predictions) if details else total / max(1, n)

    best = float("inf")
    best_state = None
    for ep in range(args.epochs):
        total, n = 0.0, 0
        model.train()
        for batch in train_dl:
            batch = to_device(batch)
            opt.zero_grad()
            pred = model(batch)
            loss = F.mse_loss(pred, batch["search_pressure"])
            loss.backward()
            opt.step()
            total += loss.item() * len(pred)
            n += len(pred)
        val = run_val()
        rel = "beats" if val < baseline_val else "worse_than"
        print(f"epoch {ep+1}/{args.epochs} train={total/max(1,n):.5f} val={val:.5f} {rel}_baseline={baseline_val:.5f}")
        if val < best:
            best = val
            best_state = {k: v.detach().cpu().clone() for k, v in model.head.state_dict().items()}
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            torch.save({
                "kind": "leaf_search_pressure_sidecar_v1",
                "head": best_state,
                "val": best,
                "validated": False,
            }, out)
    model.head.load_state_dict(best_state)
    _, predictions = run_val(details=True)
    per_source = {}
    for source in source_baselines:
        pairs = [
            (pred, row_target(row))
            for pred, row in zip(predictions, val_rows)
            if row_source(row) == source
        ]
        mse = sum((pred - target) ** 2 for pred, target in pairs) / len(pairs)
        per_source[source] = mse
        print(f"holdout {source}: mse={mse:.5f} baseline={source_baselines[source]:.5f}")
    targets_sorted = sorted(row_target(row) for row in val_rows)
    threshold = targets_sorted[max(0, int(0.75 * (len(targets_sorted) - 1)))]
    k = max(1, len(val_rows) // 4)
    predicted_top = sorted(range(len(predictions)), key=lambda i: predictions[i], reverse=True)[:k]
    high_recall = sum(row_target(val_rows[i]) >= threshold for i in predicted_top) / k
    sources_pass = all(per_source[s] < source_baselines[s] for s in per_source)
    validated = best < baseline_val and sources_pass and high_recall > 0.25
    torch.save({
        "kind": "leaf_search_pressure_sidecar_v1",
        "head": best_state,
        "val": best,
        "validated": validated,
        "holdout_mse": per_source,
        "holdout_baseline": source_baselines,
        "high_pressure_recall_at_quartile": high_recall,
    }, Path(args.out))
    print(f"best val={best:.5f} high_pressure_recall@quartile={high_recall:.1%} validated={validated} -> {args.out}")
    return 0 if validated else 2


if __name__ == "__main__":
    raise SystemExit(main())
