"""HalfPW (gen13 ACE) net — Python port of the engine forward pass.

Must match `acev13/search.rs::evaluate` bit-for-bit (`parity_check.py`).

Field plane names: see `training/field_planes.py` and `engine/src/acev13/field_planes.rs`.
Blob: 11 planes × 81×32 (goal_inv, pawn_fwd, corridor_delta, path_cross, choke×2, contested).
"""

import struct
from dataclasses import dataclass

from field_planes import (
    CHOKE_P0,
    CHOKE_P1,
    CONTESTED,
    CORRIDOR_DELTA_P0,
    CORRIDOR_DELTA_P1,
    encode_contested,
    FIELD_PLANE_COUNT,
    GOAL_INV_P0,
    GOAL_INV_P1,
    PATH_CROSS_P0,
    PATH_CROSS_P1,
    PAWN_FWD_P0,
    PAWN_FWD_P1,
    rec_field,
)

NET_H = 32
WSKIP_LEN = 16
W1C_LEN = 9 * 128 * NET_H
PO_LEN = 81 * NET_H
PX_LEN = 81 * NET_H
FIELD_LEN = 81 * NET_H
NET_WEIGHT_F64S = WSKIP_LEN + NET_H + NET_H + W1C_LEN + PO_LEN + PX_LEN + FIELD_LEN * FIELD_PLANE_COUNT

NET_MIRC = [(8 - i // 9) * 9 + i % 9 for i in range(81)]
NET_MIRS = [(7 - i // 8) * 8 + i % 8 for i in range(64)]
NET_BKT = [(i // 9 // 3) * 3 + (i % 9) // 3 for i in range(81)]
LEGAL_WALL_SLOTS = 128


def legal_wall_norm(rec: dict) -> float:
    """ws[14] input — engine JSON only; no corridor_width fallback."""
    if "legal_wall_count" not in rec:
        raise KeyError(
            "legal_wall_count missing in record — rebuild native titanium and re-run eval-batch"
        )
    return rec["legal_wall_count"] / LEGAL_WALL_SLOTS


def opponent_corridor_width(rec: dict, me: int, _d_me_i: int, d_opp_i: int) -> int:
    """ws[15] input — opponent cells on their shortest-path rank."""
    d0f = rec_field(rec, GOAL_INV_P0)
    d1f = rec_field(rec, GOAL_INV_P1)
    field = d1f if me == 0 else d0f
    return sum(1 for d in field if d == d_opp_i)


@dataclass
class Net:
    ws: list
    b1: list
    w2: list
    w1c: list
    po: list
    px: list
    goal_inv_p0: list
    goal_inv_p1: list
    pawn_fwd_p0: list
    pawn_fwd_p1: list
    corridor_delta_p0: list
    corridor_delta_p1: list
    path_cross_p0: list
    path_cross_p1: list
    choke_p0: list
    choke_p1: list
    contested: list

    @staticmethod
    def load(path):
        with open(path, "rb") as f:
            raw = f.read()
        assert len(raw) == NET_WEIGHT_F64S * 8, (
            f"size {len(raw)} != {NET_WEIGHT_F64S * 8} — run training/extend_field_planes.py"
        )
        vals = list(struct.unpack(f"<{NET_WEIGHT_F64S}d", raw))
        o = 0

        def take(n):
            nonlocal o
            s = vals[o:o + n]
            o += n
            return s

        return Net(
            take(WSKIP_LEN), take(NET_H), take(NET_H),
            take(W1C_LEN), take(PO_LEN), take(PX_LEN),
            take(FIELD_LEN), take(FIELD_LEN), take(FIELD_LEN), take(FIELD_LEN),
            take(FIELD_LEN), take(FIELD_LEN), take(FIELD_LEN), take(FIELD_LEN),
            take(FIELD_LEN), take(FIELD_LEN), take(FIELD_LEN),
        )


def _cell_feats(goal_f, player_f, delta_f, cross_f, choke_f) -> tuple:
    gf, pf, df, cf, chf = [], [], [], [], []
    for i in range(81):
        dg = goal_f[i] if i < len(goal_f) else 255
        if dg == 255:
            gf.append(0.0)
            pf.append(0.0)
            df.append(0.0)
            cf.append(0.0)
            chf.append(0.0)
            continue
        gf.append(dg / 16.0)
        ps = player_f[i] if i < len(player_f) else 255
        pf.append(0.0 if ps == 255 else ps / 16.0)
        dt = delta_f[i] if i < len(delta_f) else 255
        df.append(0.0 if dt == 255 else dt / 16.0)
        cv = cross_f[i] if i < len(cross_f) else 0
        cf.append(0.0 if not cv else cv / 16.0)
        hv = choke_f[i] if i < len(choke_f) else 0
        chf.append(hv / 16.0 if hv else 0.0)
    return gf, pf, df, cf, chf


def _contested_vec(delta0_raw, delta1_raw, contested_raw) -> list[float]:
    out = []
    for i in range(81):
        if contested_raw and i < len(contested_raw) and contested_raw[i]:
            out.append(contested_raw[i] / 16.0)
            continue
        d0 = delta0_raw[i] if i < len(delta0_raw) else 255
        d1 = delta1_raw[i] if i < len(delta1_raw) else 255
        out.append(encode_contested(d0, d1))
    return out


def _field_plane_contrib(net: Net, hid: list[float], rec: dict) -> None:
    g0 = rec_field(rec, GOAL_INV_P0)
    g1 = rec_field(rec, GOAL_INV_P1)
    p0 = rec_field(rec, PAWN_FWD_P0)
    p1 = rec_field(rec, PAWN_FWD_P1)
    d0 = rec_field(rec, CORRIDOR_DELTA_P0)
    d1 = rec_field(rec, CORRIDOR_DELTA_P1)
    c0 = rec_field(rec, PATH_CROSS_P0)
    c1 = rec_field(rec, PATH_CROSS_P1)
    k0 = rec_field(rec, CHOKE_P0)
    k1 = rec_field(rec, CHOKE_P1)
    ct = rec_field(rec, CONTESTED)
    gf0, pf0, df0, cf0, ch0 = _cell_feats(g0, p0, d0, c0, k0)
    gf1, pf1, df1, cf1, ch1 = _cell_feats(g1, p1, d1, c1, k1)
    contested = _contested_vec(d0, d1, ct)
    for i in range(81):
        base = i * NET_H
        for j in range(NET_H):
            hid[j] += (
                net.goal_inv_p0[base + j] * gf0[i]
                + net.pawn_fwd_p0[base + j] * pf0[i]
                + net.corridor_delta_p0[base + j] * df0[i]
                + net.path_cross_p0[base + j] * cf0[i]
                + net.choke_p0[base + j] * ch0[i]
                + net.goal_inv_p1[base + j] * gf1[i]
                + net.pawn_fwd_p1[base + j] * pf1[i]
                + net.corridor_delta_p1[base + j] * df1[i]
                + net.path_cross_p1[base + j] * cf1[i]
                + net.choke_p1[base + j] * ch1[i]
                + net.contested[base + j] * contested[i]
            )


def forward(net, rec):
    """Reproduce the engine's walls-present net eval for one feature record."""
    me = rec["turn"]
    opp = 1 - me
    wl = [rec["wl0"], rec["wl1"]]
    dist = [rec["d0"], rec["d1"]]
    d_me = float(dist[me])
    d_opp = float(dist[opp])
    w_me = float(wl[me])
    w_opp = float(wl[opp])
    ws = net.ws

    pd = d_opp - d_me
    wd = w_me - w_opp
    out = (ws[0] + ws[1] * pd + ws[2] * wd + ws[3] * d_me + ws[4] * d_opp
           + ws[9] * pd * (w_me + w_opp) / 20.0
           + ws[10] * wd * (d_me + d_opp) / 16.0)
    if w_opp == 0.0:
        out += ws[6]
        if d_me <= d_opp:
            out += ws[5]
    elif w_me == 0.0:
        out += ws[8]
        if d_opp <= d_me - 1.0:
            out += ws[7]
    if d_opp <= 4.0:
        out += ws[11] * (w_me if w_me < 3.0 else 3.0)
    if d_me <= 4.0:
        out += ws[12] * (w_opp if w_opp < 3.0 else 3.0)

    out += ws[13] * pd * w_opp / 10.0

    d_me_i = int(d_me)
    d_opp_i = int(d_opp)
    out += ws[14] * legal_wall_norm(rec)
    out += ws[15] * opponent_corridor_width(rec, me, d_me_i, d_opp_i)

    pawn0, pawn1 = rec["pawn0"], rec["pawn1"]
    hw, vw = rec["hw"], rec["vw"]
    hid = [0.0] * NET_H

    if me == 0:
        b0 = NET_BKT[pawn0]
        acc = [0.0] * NET_H
        for s in range(64):
            if hw[s]:
                o = (b0 * 128 + s) * NET_H
                for j in range(NET_H):
                    acc[j] += net.w1c[o + j]
            if vw[s]:
                o = (b0 * 128 + 64 + s) * NET_H
                for j in range(NET_H):
                    acc[j] += net.w1c[o + j]
        po0 = pawn0 * NET_H
        px1 = pawn1 * NET_H
        for j in range(NET_H):
            hid[j] = net.b1[j] + acc[j] + net.po[po0 + j] + net.px[px1 + j]
    else:
        b1v = NET_BKT[NET_MIRC[pawn1]]
        acc = [0.0] * NET_H
        for s in range(64):
            if hw[s]:
                o = (b1v * 128 + NET_MIRS[s]) * NET_H
                for j in range(NET_H):
                    acc[j] += net.w1c[o + j]
            if vw[s]:
                o = (b1v * 128 + 64 + NET_MIRS[s]) * NET_H
                for j in range(NET_H):
                    acc[j] += net.w1c[o + j]
        po0 = NET_MIRC[pawn1] * NET_H
        px1 = NET_MIRC[pawn0] * NET_H
        for j in range(NET_H):
            hid[j] = net.b1[j] + acc[j] + net.po[po0 + j] + net.px[px1 + j]

    _field_plane_contrib(net, hid, rec)

    for j in range(NET_H):
        a2 = min(1.0, max(0.0, hid[j]))
        out += net.w2[j] * a2 * 200.0

    return int(out)
