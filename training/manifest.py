"""Training-data manifest — Elo tracker + global rating ladder.

Per matchup: only a_wins / b_wins (+ optional time-control labels).
Elo diff recomputed from W/L ratio. Global ladder propagates diffs from
anchor ace-v13-ti-pure@5s = 1400 (Quoridor Pro–style scale; see ANCHOR_RATING).
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "training" / "data"
MANIFEST_PATH = DATA / "manifest.json"
STATUS_PATH = DATA / "STATUS.txt"
LOCK_PATH = DATA / "manifest.lock"

CURRENT_ENGINE = "titanium-v15"
BASELINE_ENGINE = "ace-v13-ti-pure"
# Bare "titanium" = legacy GameSearchSession (MCTS), NOT v15 or ace-v13 — exclude from ladder.
DEPRECATED_LADDER_ENGINES = frozenset({"titanium", "titanium-cert", "titanium-plain"})
ANCHOR_ENTITY = f"{BASELINE_ENGINE}@5s"
REMOTE_ENGINES = frozenset({"ka", "ishtar"})
# Site UI labels for Ka/Ishtar time presets (strength fixed at Alpha on wire).
REMOTE_TIME_LABELS = {
    "intuition": "Immediate",
    "immediate": "Immediate",
    "short": "Short",
    "medium": "Medium",
    "long": "Long",
}
# quoridor.pro starts new players at 1400; top humans ~1450–1490 (2024–2026 leaderboard).
# ti-pure = JS v13 + O1 movegen only → pinned as “default online player”, not chess-club 1600.
ANCHOR_RATING = 1400.0
MIN_GAMES_GLOBAL = 2  # include Ka/remote on ladder after a few games
MIN_GAMES_LADDER_STABLE = 4  # shown as note in STATUS when below this

PATHS = {
    "training_db": str(DATA / "all_games.db"),
    "benchmark_log": str(DATA / "benchmarks_log.jsonl"),
    "strength_tracker_games": str(DATA / "v15_vs_ti_pure.games"),
    "self_match_games": str(DATA / "self_match_games.games"),
    "benchmark_games_dir": str(DATA / "benchmarks"),
    "tournament_games_dir": str(DATA / "tournament"),
}

_LEGACY_STRENGTH_GAMES = DATA / "v14_vs_ti_pure.games"
_LEGACY_MANIFEST_KEY = "v14_vs_ti_pure"


def entity_label(engine: str, tc: str | None) -> str:
    tc = (tc or "5s").strip()
    return f"{engine}@{tc}"


def is_deprecated_engine(engine: str) -> bool:
    """Legacy session flags that are not ace-v13 family or titanium-v15."""
    return engine.split("@", 1)[0] in DEPRECATED_LADDER_ENGINES


def is_deprecated_entity(ent: str) -> bool:
    return is_deprecated_engine(ent.split("@", 1)[0])


def display_entity(ent: str) -> str:
    """Scoreboard-friendly label (remote presets show UI time name)."""
    if "@" not in ent:
        return ent
    base, tc = ent.split("@", 1)
    if base in REMOTE_ENGINES:
        ui = REMOTE_TIME_LABELS.get(tc, tc)
        return f"{base}@{tc} ({ui} @ Alpha)"
    return ent


def matchup_key(
    engine_a: str,
    engine_b: str,
    tc_a: str | None = None,
    tc_b: str | None = None,
) -> str:
    return f"{engine_a}|{engine_b}|{tc_a or '5s'}|{tc_b or '5s'}"


def _legacy_matchup_key(engine_a: str, engine_b: str) -> str:
    return f"{engine_a}|{engine_b}"


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    if path.suffix == ".db":
        import sqlite3
        try:
            conn = sqlite3.connect(str(path))
            n = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
            conn.close()
            return n
        except Exception:
            return 0
    with open(path, encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def count_games_in_file(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("RESULT ") and line.split()[1] in ("W", "B"):
                n += 1
    return n


def elo_a_vs_b(a_wins: int, b_wins: int) -> float | None:
    n = a_wins + b_wins
    if n == 0:
        return None
    # Pseudocount avoids None at 0% / 100% (needed for Ka@medium/long on global ladder).
    p = (a_wins + 0.5) / (n + 1)
    if p <= 0 or p >= 1:
        return None
    import math
    return round(-400 * math.log10((1 - p) / p), 1)


def _migrate_legacy_strength(manifest: dict) -> dict:
    if "strength_tracker" in manifest:
        return manifest
    legacy = manifest.pop(_LEGACY_MANIFEST_KEY, None)
    if legacy:
        manifest["strength_tracker"] = legacy
    return manifest


def _canonical_tc(engine: str, tc: str | None) -> str:
    """Drop legacy fair-* labels — v15 is always tracked as @5s on the ladder."""
    tc = (tc or "5s").strip()
    if tc.startswith("fair-"):
        return "5s"
    return tc


def _normalize_matchups(manifest: dict) -> dict:
    """Merge legacy 2-part keys; fold fair-* tc_a into 5s."""
    raw = manifest.get("matchups", {})
    out: dict = {}

    def merge_into(nk: str, entry: dict) -> None:
        if nk in out:
            out[nk]["a_wins"] = out[nk].get("a_wins", 0) + entry.get("a_wins", 0)
            out[nk]["b_wins"] = out[nk].get("b_wins", 0) + entry.get("b_wins", 0)
            out[nk]["games_played"] = out[nk]["a_wins"] + out[nk]["b_wins"]
            out[nk]["elo_a_vs_b"] = elo_a_vs_b(out[nk]["a_wins"], out[nk]["b_wins"])
            for k in ("games_file", "last_source", "games_in_file"):
                if entry.get(k):
                    out[nk][k] = entry[k]
        else:
            out[nk] = entry

    for key, entry in raw.items():
        entry = dict(entry)
        parts = key.split("|")
        if len(parts) == 2:
            nk = matchup_key(parts[0], parts[1])
            entry.setdefault("tc_a", "5s")
            entry.setdefault("tc_b", "5s")
            entry["a_engine"] = parts[0]
            entry["b_engine"] = parts[1]
            merge_into(nk, entry)
        elif len(parts) >= 4:
            ea, eb = parts[0], parts[1]
            tc_a = _canonical_tc(ea, entry.get("tc_a", parts[2]))
            tc_b = _canonical_tc(eb, entry.get("tc_b", parts[3]))
            entry["tc_a"] = tc_a
            entry["tc_b"] = tc_b
            entry["a_engine"] = ea
            entry["b_engine"] = eb
            nk = matchup_key(ea, eb, tc_a, tc_b)
            merge_into(nk, entry)
        else:
            out[key] = entry
    for entry in out.values():
        aw, bw = entry.get("a_wins", 0), entry.get("b_wins", 0)
        entry["games_played"] = aw + bw
        entry["elo_a_vs_b"] = elo_a_vs_b(aw, bw)
    manifest["matchups"] = out
    return manifest


def aggregate_entity_wl(matchups: dict) -> dict[str, dict]:
    stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0})
    for m in matchups.values():
        tc_a = m.get("tc_a", "5s")
        tc_b = m.get("tc_b", "5s")
        ea = entity_label(m["a_engine"], tc_a)
        eb = entity_label(m["b_engine"], tc_b)
        aw, bw = m.get("a_wins", 0), m.get("b_wins", 0)
        stats[ea]["wins"] += aw
        stats[ea]["losses"] += bw
        stats[eb]["wins"] += bw
        stats[eb]["losses"] += aw
        stats[ea]["games"] = stats[ea]["wins"] + stats[ea]["losses"]
        stats[eb]["games"] = stats[eb]["wins"] + stats[eb]["losses"]
    return dict(stats)


def compute_global_ratings(matchups: dict) -> dict[str, dict]:
    """Naive fixed-point propagation from anchor through measured matchup edges."""
    edges: list[tuple[str, str, float]] = []
    entities: set[str] = {ANCHOR_ENTITY}

    for m in matchups.values():
        n = m.get("games_played", m.get("a_wins", 0) + m.get("b_wins", 0))
        if n < MIN_GAMES_GLOBAL:
            continue
        diff = m.get("elo_a_vs_b")
        if diff is None:
            continue
        ea = entity_label(m["a_engine"], m.get("tc_a", "5s"))
        eb = entity_label(m["b_engine"], m.get("tc_b", "5s"))
        if is_deprecated_entity(ea) or is_deprecated_entity(eb):
            continue
        entities.add(ea)
        entities.add(eb)
        edges.append((ea, eb, float(diff)))

    # Same engine @ different time controls = same strength (link for graph propagation).
    by_base: dict[str, set[str]] = defaultdict(set)
    for ent in list(entities):
        base = ent.split("@", 1)[0]
        by_base[base].add(ent)
    anchor_v15 = entity_label(CURRENT_ENGINE, "5s")
    for ent in list(entities):
        base = ent.split("@", 1)[0]
        if base == CURRENT_ENGINE and ent != anchor_v15:
            entities.add(anchor_v15)
            edges.append((ent, anchor_v15, 0.0))
            edges.append((anchor_v15, ent, 0.0))

    ratings: dict[str, float] = {ANCHOR_ENTITY: ANCHOR_RATING}
    for _ in range(40):
        accum: dict[str, list[float]] = defaultdict(list)
        for ea, eb, diff in edges:
            if eb in ratings:
                accum[ea].append(ratings[eb] + diff)
            if ea in ratings:
                accum[eb].append(ratings[ea] - diff)
        changed = False
        for ent, vals in accum.items():
            if ent == ANCHOR_ENTITY or not vals:
                continue
            new_r = sum(vals) / len(vals)
            if ent not in ratings or abs(ratings.get(ent, 0) - new_r) > 0.05:
                changed = True
            ratings[ent] = new_r
        if not changed:
            break

    # Same engine @ different time controls = same strength (our engine only; remotes rank per preset).
    bases: dict[str, list[float]] = defaultdict(list)
    for ent, r in ratings.items():
        base = ent.split("@", 1)[0]
        if base in REMOTE_ENGINES:
            continue
        bases[base].append(r)
    base_rating = {b: sum(v) / len(v) for b, v in bases.items() if v}
    for ent in entities:
        if ent in ratings:
            continue
        base = ent.split("@", 1)[0]
        if base in REMOTE_ENGINES:
            continue
        if base in base_rating:
            ratings[ent] = base_rating[base]

    # Fill remaining nodes one edge away from any rated entity.
    for _ in range(10):
        for ea, eb, diff in edges:
            if eb in ratings and ea not in ratings:
                ratings[ea] = ratings[eb] + diff
            elif ea in ratings and eb not in ratings:
                ratings[eb] = ratings[ea] - diff

    wl = aggregate_entity_wl(matchups)
    out: dict[str, dict] = {}
    for ent in entities:
        if ent not in ratings or is_deprecated_entity(ent):
            continue
        s = wl.get(ent, {})
        out[ent] = {
            "rating": round(ratings[ent]),
            "wins": s.get("wins", 0),
            "losses": s.get("losses", 0),
            "games": s.get("games", 0),
            "anchor": ent == ANCHOR_ENTITY,
            "provisional": s.get("games", 0) < MIN_GAMES_LADDER_STABLE,
        }
    return out


@contextmanager
def manifest_lock(timeout_sec: float = 30.0):
    """Exclusive lock for manifest read-modify-write (parallel game updates)."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            time.sleep(0.05)
    else:
        raise RuntimeError("manifest lock timeout")
    try:
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    else:
        manifest = {"paths": PATHS, "sources": {}, "matchups": {}, "global_ratings": {}}
    manifest["paths"] = PATHS
    manifest["current_engine"] = CURRENT_ENGINE
    manifest["baseline_engine"] = BASELINE_ENGINE
    manifest["anchor_entity"] = ANCHOR_ENTITY
    manifest["anchor_rating"] = ANCHOR_RATING
    manifest = _migrate_legacy_strength(manifest)
    manifest = _normalize_matchups(manifest)
    manifest["global_ratings"] = compute_global_ratings(manifest.get("matchups", {}))
    return manifest


def _write_manifest(manifest: dict) -> None:
    manifest["global_ratings"] = compute_global_ratings(manifest.get("matchups", {}))
    DATA.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["paths"] = PATHS
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _write_status_txt(manifest)


def save_manifest(manifest: dict) -> None:
    with manifest_lock():
        _write_manifest(manifest)


def lookup_prior_wins(
    engine_a: str,
    engine_b: str,
    tc_a: str | None = None,
    tc_b: str | None = None,
) -> tuple[int, int]:
    manifest = load_manifest()
    matchups = manifest.get("matchups", {})
    for key in (matchup_key(engine_a, engine_b, tc_a, tc_b), _legacy_matchup_key(engine_a, engine_b)):
        m = matchups.get(key)
        if m:
            return m.get("a_wins", 0), m.get("b_wins", 0)
    return 0, 0


def update_matchup(
    engine_a: str,
    engine_b: str,
    a_wins: int,
    b_wins: int,
    tc_a: str | None = None,
    tc_b: str | None = None,
    games_file: str | Path | None = None,
    source: str | None = None,
) -> dict:
    with manifest_lock():
        manifest = load_manifest()
        tc_a = tc_a or "5s"
        tc_b = tc_b or "5s"
        key = matchup_key(engine_a, engine_b, tc_a, tc_b)
        n = a_wins + b_wins
        entry = {
            "a_engine": engine_a,
            "b_engine": engine_b,
            "tc_a": tc_a,
            "tc_b": tc_b,
            "a_wins": a_wins,
            "b_wins": b_wins,
            "games_played": n,
            "elo_a_vs_b": elo_a_vs_b(a_wins, b_wins),
        }
        if games_file:
            entry["games_file"] = str(games_file)
            entry["games_in_file"] = count_games_in_file(Path(games_file))
        if source:
            entry["last_source"] = source
        manifest.setdefault("matchups", {})[key] = entry
        legacy = _legacy_matchup_key(engine_a, engine_b)
        if legacy in manifest["matchups"] and legacy != key:
            del manifest["matchups"][legacy]
        if engine_a == CURRENT_ENGINE and engine_b == BASELINE_ENGINE and tc_a == "5s" and tc_b == "5s":
            manifest["strength_tracker"] = {**entry, "elo_vs_baseline": entry.get("elo_a_vs_b")}
        _write_manifest(manifest)
        return entry


def update_strength_tracker(a_wins: int, b_wins: int, batch: int | None = None, **_kw) -> None:
    update_matchup(CURRENT_ENGINE, BASELINE_ENGINE, a_wins, b_wins, "5s", "5s")
    if batch is not None:
        manifest = load_manifest()
        k = matchup_key(CURRENT_ENGINE, BASELINE_ENGINE, "5s", "5s")
        manifest["matchups"][k]["batches_completed"] = batch
        save_manifest(manifest)


update_v15_vs_ti_pure = update_strength_tracker


def update_source(name: str, games_file: str | Path, **extra) -> None:
    games_file = Path(games_file)
    manifest = load_manifest()
    manifest.setdefault("sources", {})[name] = {
        "games_file": str(games_file),
        "games": count_games_in_file(games_file),
        "bytes": games_file.stat().st_size if games_file.exists() else 0,
        **extra,
    }
    save_manifest(manifest)


def _format_tc(tc_a: str, tc_b: str) -> str:
    if tc_a == tc_b:
        return tc_a
    return f"A:{tc_a} B:{tc_b}"


def format_scoreboard(manifest: dict) -> str:
    """Terminal-friendly ladder + matchup W/L (same data as STATUS.txt core)."""
    global_ratings = manifest.get("global_ratings", {})
    matchups = manifest.get("matchups", {})
    t = manifest.get("tournament", {})
    db_records = _count_lines(Path(PATHS["training_db"]))

    lines = [
        "",
        "=" * 72,
        f" SCOREBOARD   anchor {ANCHOR_ENTITY} = {int(ANCHOR_RATING)} Elo   |   {db_records} games in DB",
        "=" * 72,
    ]
    if t:
        mode = t.get("mode", "random")
        if mode == "random":
            batch = t.get("batch", 0)
            last = t.get("last_batch") or []
            par = t.get("parallel", 4)
            lines.append(f" Random batch #{batch}  |  {par} parallel matchups, 1 game each")
            if last:
                lines.append(f" Last batch: {', '.join(last)}")
        elif mode == "random-pool":
            games = t.get("games", 0)
            par = t.get("parallel", 4)
            lines.append(f" Continuous pool  |  {par} independent slots  |  {games} games completed")
        elif mode == "round_robin":
            cycle = t.get("cycle", 1)
            idx = t.get("cycle_index", 0)
            total = t.get("cycle_total") or "?"
            slot = (idx % int(total)) + 1 if total != "?" and int(total) else "?"
            lines.append(
                f" Round-robin cycle {cycle}  |  next slot {slot}/{total}"
            )
            lines.append(f" Last pairing: {t.get('last_pairing', '?')}")
            lines.append(f" Next pairing: {t.get('next_pairing', '?')}")
        else:
            lines.append(
                f" Swiss round {t.get('round', '?')}  |  last: {t.get('last_pairing', '?')} ({t.get('last_kind', '?')})"
            )
            lines.append(f" Last pairing: {t.get('last_pairing', '?')}")
        lines.append("-" * 72)

    if global_ratings:
        lines.append(" GLOBAL LADDER")
        ranked = sorted(
            ((k, v) for k, v in global_ratings.items() if not is_deprecated_entity(k)),
            key=lambda x: -x[1]["rating"],
        )
        for i, (ent, info) in enumerate(ranked, 1):
            w, l, g = info.get("wins", 0), info.get("losses", 0), info.get("games", 0)
            tag = " [anchor]" if info.get("anchor") else ""
            if info.get("provisional"):
                tag += " [prov]"
            wr = f"{100 * w / g:.0f}%" if g else "?"
            lines.append(
                f"  #{i:<2} {display_entity(ent):<34} {info['rating']:>4} Elo   {w}-{l}  ({g}g, {wr}){tag}"
            )
        cur = entity_label(CURRENT_ENGINE, "5s")
        if cur in global_ratings:
            delta = global_ratings[cur]["rating"] - int(ANCHOR_RATING)
            sign = "+" if delta >= 0 else ""
            lines.append(f"  >> {cur} = {global_ratings[cur]['rating']} ({sign}{delta} vs anchor)")
        lines.append("-" * 72)

    if matchups:
        lines.append(" MATCHUPS (A wins - B wins)")
        rows = []
        for key in sorted(matchups.keys()):
            m = matchups[key]
            aw, bw = m.get("a_wins", 0), m.get("b_wins", 0)
            n = m.get("games_played", aw + bw)
            if n == 0:
                continue
            if is_deprecated_engine(m.get("a_engine", "")) or is_deprecated_engine(m.get("b_engine", "")):
                continue
            elo = m.get("elo_a_vs_b")
            elo_s = f"{elo:+.0f}" if elo is not None else "?"
            se = ((aw / n * (1 - aw / n)) / n) ** 0.5 * 196 if n else 0
            tc = _format_tc(m.get("tc_a", "5s"), m.get("tc_b", "5s"))
            label = f"{m['a_engine']} vs {m['b_engine']}"
            rows.append((label, tc, aw, bw, n, elo_s, se))
        for label, tc, aw, bw, n, elo_s, se in rows:
            lines.append(
                f"  {label:<28} ({tc:<14})  {aw:>3}-{bw:<3}  {n:>4}g  ~{elo_s} diff (+/-{se:.0f}%)"
            )
    else:
        lines.append(" (no matchups yet)")

    lines.append("=" * 72)
    lines.append("")
    return "\n".join(lines)


def _write_status_txt(manifest: dict) -> None:
    db_records = _count_lines(Path(PATHS["training_db"]))
    matchups = manifest.get("matchups", {})
    global_ratings = manifest.get("global_ratings", {})

    lines = [
        "=== Quoridor training data ===",
        f"Updated: {manifest.get('updated_at', '?')}",
        f"Anchor: {ANCHOR_ENTITY} = {int(ANCHOR_RATING)} Elo",
        "",
        "KEY FILES (training/data/):",
        f"  Training DB:     all_games.db  ({db_records} games)",
        f"  Elo manifest:    manifest.json",
        f"  This summary:    STATUS.txt",
        "",
    ]

    t = manifest.get("tournament", {})
    if t:
        lines += [
            "SWISS TOURNAMENT (overnight):",
            f"  Round {t.get('round', '?')}  last: {t.get('last_pairing', '?')} ({t.get('last_kind', '?')})",
            f"  Next: {t.get('next_pairing', '?')}",
            "",
        ]

    if global_ratings:
        lines.append(
            f"GLOBAL RATING LADDER (~Quoridor Pro scale, anchor {int(ANCHOR_RATING)}; "
            "direct H2H is more precise per pairing):"
        )
        ranked = sorted(
            ((k, v) for k, v in global_ratings.items() if not is_deprecated_entity(k)),
            key=lambda x: -x[1]["rating"],
        )
        for i, (ent, info) in enumerate(ranked, 1):
            w, l, g = info.get("wins", 0), info.get("losses", 0), info.get("games", 0)
            anchor = "  [anchor]" if info.get("anchor") else ""
            if info.get("provisional"):
                anchor += "  [prov]"
            wr = f"{100 * w / g:.0f}%" if g else "?"
            lines.append(
                f"  #{i:<2} {ent:<32} {info['rating']:>4} Elo  {w}-{l} ({g}g, {wr} win){anchor}"
            )
        if ranked:
            top = ranked[0][0]
            cur = entity_label(CURRENT_ENGINE, "5s")
            if cur in global_ratings:
                lines.append(
                    f"  >> {cur} = {global_ratings[cur]['rating']} Elo "
                    f"(+{global_ratings[cur]['rating'] - int(ANCHOR_RATING)} vs anchor)"
                )
        lines.append("")

    if matchups:
        lines.append("MATCHUP DETAILS (direct W/L → Elo diff):")
        for key in sorted(matchups.keys()):
            m = matchups[key]
            aw, bw = m.get("a_wins", 0), m.get("b_wins", 0)
            n = m.get("games_played", aw + bw)
            elo = m.get("elo_a_vs_b")
            elo_s = f"{elo:+.0f}" if elo is not None else "?"
            se = ((aw / n * (1 - aw / n)) / n) ** 0.5 * 196 if n else 0
            tc = _format_tc(m.get("tc_a", "5s"), m.get("tc_b", "5s"))
            src = m.get("last_source", "")
            src_s = f" [{src}]" if src else ""
            lines.append(
                f"  {m['a_engine']} vs {m['b_engine']} ({tc}): "
                f"{aw}-{bw} / {n}g  ~{elo_s} diff (±{se:.0f}%){src_s}"
            )
        lines.append("")

    sources = manifest.get("sources", {})
    if sources:
        lines.append("GAME SOURCES:")
        for name, info in sorted(sources.items()):
            lines.append(f"  {name}: {info.get('games', 0)} games -> {info.get('games_file', '?')}")
        lines.append("")

    STATUS_PATH.write_text("\n".join(lines), encoding="utf-8")


def _cli_update_matchup(args) -> None:
    update_matchup(
        args.engine_a, args.engine_b, args.a_wins, args.b_wins,
        tc_a=args.tc_a, tc_b=args.tc_b,
        games_file=args.games_file, source=args.source,
    )
    m = load_manifest()["matchups"][matchup_key(args.engine_a, args.engine_b, args.tc_a, args.tc_b)]
    elo = m.get("elo_a_vs_b")
    elo_s = f"{elo:+.0f}" if elo is not None else "?"
    gr = load_manifest().get("global_ratings", {})
    ea = entity_label(args.engine_a, args.tc_a or "5s")
    eb = entity_label(args.engine_b, args.tc_b or "5s")
    ga = gr.get(ea, {}).get("rating", "?")
    gb = gr.get(eb, {}).get("rating", "?")
    print(
        f"{args.engine_a} {args.a_wins}-{args.b_wins} {args.engine_b} ({args.tc_a}|{args.tc_b}) "
        f"/ {m['games_played']}g  diff ~{elo_s}  ladder {ea}={ga} {eb}={gb}"
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-matchup", action="store_true")
    ap.add_argument("--lookup-prior", action="store_true")
    ap.add_argument("--engine-a", required=True)
    ap.add_argument("--engine-b", required=True)
    ap.add_argument("--a-wins", type=int, default=None)
    ap.add_argument("--b-wins", type=int, default=None)
    ap.add_argument("--tc-a", default="5s")
    ap.add_argument("--tc-b", default="5s")
    ap.add_argument("--games-file", default=None)
    ap.add_argument("--source", default=None)
    args = ap.parse_args()
    if args.lookup_prior:
        aw, bw = lookup_prior_wins(args.engine_a, args.engine_b, args.tc_a, args.tc_b)
        print(json.dumps({"a_wins": aw, "b_wins": bw}))
    elif args.update_matchup:
        if args.a_wins is None or args.b_wins is None:
            ap.error("--a-wins and --b-wins required with --update-matchup")
        _cli_update_matchup(args)
