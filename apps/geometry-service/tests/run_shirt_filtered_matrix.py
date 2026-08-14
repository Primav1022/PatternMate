"""15-cell shirt matrix: 3 templates × collar/placket/silhouette/sleeve/cuff.

Collar+placket = whole front/back swap; silhouette = side-seam morph; sleeve/cuff = piece swap.
Donors pre-filtered by part label.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import IR_INDEX
from run_shirt_remix_matrix import (
    BODIES,
    COLLAR_ALTS,
    CUFF_ALTS,
    FABRICS,
    PLACKET_ALTS,
    SILHOUETTE_ALTS,
    SLEEVE_ALTS,
    TILE_DIR,
    base_options,
    pick_alt,
    render_tile,
    stitch,
)

HOSTS = ["C2431105", "C2330115", "C2530029"]
GROUPS = ["collar", "placket", "silhouette", "sleeve", "cuff"]
ALTS = {
    "collar": COLLAR_ALTS,
    "placket": PLACKET_ALTS,
    "silhouette": SILHOUETTE_ALTS,
    "sleeve": SLEEVE_ALTS,
    "cuff": CUFF_ALTS,
}
OUT = Path(__file__).resolve().parent / "shirt_remix_logic_15.png"


def main() -> None:
    body = BODIES["woman"]
    tiles = []
    for host in HOSTS:
        if host not in IR_INDEX:
            print("skip missing", host)
            continue
        ir = IR_INDEX[host]
        base_opts = base_options(host)
        for group in GROUPS:
            picked = pick_alt(group, ir, base_opts.get(group), ALTS[group])
            sel = dict(base_opts)
            if picked:
                sel[group] = picked[1]
                caption = f"{group} {picked[1].split('.')[-1]}\n← {picked[2]}"
            else:
                caption = f"{group} (no donor)"
            recipe = {
                "family": "shirt",
                "sex": body["sex"],
                "base_case_id": host,
                "measurements_cm": body["measurements_cm"],
                "fit": body["fit"],
                "ease_cm": body["ease"],
                "material_id": FABRICS["poplin"],
                "fabric_color": "#ffffff",
                "selections": sel,
                "base_option_ids": base_opts,
                "intent_constraints": {},
                "execution_mode": "shirt_strategy",
                "compact_layout": True,
            }
            png = TILE_DIR / f"logic_{host}_{group}.png"
            status, extra, meta = render_tile(recipe, png)
            src = (meta.get("sources") or {}).get(group) or {}
            print(
                f"{host} {group} donor={src.get('case_id')} mode={src.get('mode')} "
                f"opt={sel.get(group)} status={status} extra={extra} "
                f"caption={caption.replace(chr(10), ' ')}"
            )
            tiles.append((host, group, (png, caption, status, extra)))
    stitch(
        tiles,
        [h for h in HOSTS if h in IR_INDEX],
        GROUPS,
        "衬衫 · 领/门襟整换衣身 · 廓形改侧缝 · 袖/袖口换片",
        OUT,
    )
    print("DONE", OUT)


if __name__ == "__main__":
    main()
