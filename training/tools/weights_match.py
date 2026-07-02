"""Head-to-head match between two weight files on the same engine binary.
Usage: python weights_match.py <weights_a> <weights_b> <games> <sec_per_move>
Colors alternate; varied openings; prints running score + final Elo diff.
"""
import math
import os
import subprocess
import sys

EXE = r"C:\gitProjects\Quoridor best AI\engine\target\release\titanium.exe"
OPENINGS = [
    ["e2", "e8", "e3", "e7"],
    ["e2", "e8", "e3", "d8"],
    ["e2", "e8", "d2", "e7"],
    ["e2", "e8", "e3", "e6h"],
    ["e2", "e8", "d5h", "e7"],
    ["e2", "e8", "e3", "f8"],
    ["e2", "f8", "e3", "f7"],
    ["d1", "e8", "d2", "e7"],
    ["e2", "e8", "f2", "e7"],
    ["e2", "e8", "e3", "c6v"],
]
MAXPLY = 200

def genmove(weights, moves, sec):
    env = os.environ.copy()
    env["TITANIUM_NET_WEIGHTS_PATH"] = weights
    out = subprocess.run(
        [EXE, "genmove", "--engine", "titanium-v16", "--time", str(sec), "--book", "off"] + moves,
        capture_output=True, text=True, timeout=120, env=env,
    ).stdout
    for line in out.splitlines():
        if line.startswith("bestmove ") and "(none)" not in line:
            return line.split()[1]
    return None

def pawn_rows(moves):
    w, b = 1, 9
    for i, m in enumerate(moves):
        if len(m) == 3 and m[-1] in "hv":
            continue
        row = int(m[1:])
        if i % 2 == 0:
            w = row
        else:
            b = row
    return w, b

def play(white_w, black_w, opening, sec):
    moves = list(opening)
    while len(moves) < MAXPLY:
        w, b = pawn_rows(moves)
        if w == 9:
            return "white"
        if b == 1:
            return "black"
        weights = white_w if len(moves) % 2 == 0 else black_w
        mv = genmove(weights, moves, sec)
        if mv is None:
            return "black" if len(moves) % 2 == 0 else "white"
        moves.append(mv)
        w, b = pawn_rows(moves)
        if w == 9:
            return "white"
        if b == 1:
            return "black"
    return "draw"

def run_weights_match(weights_a: str, weights_b: str, games: int, sec: float, quiet: bool = False) -> dict:
    """Play `games` between two weight files (colors alternate, varied openings,
    book off). Returns {a_points, b_points, games, score, elo_diff}."""
    a_pts = b_pts = 0.0
    n = 0
    for i in range(games):
        opening = OPENINGS[(i // 2) % len(OPENINGS)]
        a_is_white = i % 2 == 0
        white, black = (weights_a, weights_b) if a_is_white else (weights_b, weights_a)
        result = play(white, black, opening, sec)
        n += 1
        if result == "draw":
            a_pts += 0.5
            b_pts += 0.5
        elif (result == "white") == a_is_white:
            a_pts += 1
        else:
            b_pts += 1
        if not quiet:
            print(f"game {n}: A_as_{'W' if a_is_white else 'B'} -> {result} | A {a_pts} - B {b_pts}", flush=True)
    score = a_pts / n if n else 0.5
    elo = None if score in (0, 1) or n == 0 else round(-400 * math.log10(1 / score - 1), 1)
    return {"a_points": a_pts, "b_points": b_pts, "games": n, "score": round(score, 4), "elo_diff": elo}


def main():
    wa, wb, games, sec = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    r = run_weights_match(wa, wb, games, sec)
    print(f"FINAL: A {r['a_points']} vs B {r['b_points']} over {r['games']} games ({r['score']*100:.0f}%) elo_diff={r['elo_diff']}", flush=True)

if __name__ == "__main__":
    main()
