"""Shirt remix matrix — same axes as tshirt v4: base × style × body × fabric + collages."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app import IR_INDEX, PATTERN_CATALOG, svg_for_ir
from composition_engine import filter_preview_entities, _normalize_physical_components
from donor_similarity import part_label_slug, rank_donors
from shirt_compose import compose_shirt
from simple_compose import _annotate, _mean_body_wh

OUT_DIR = Path("apps/geometry-service/tests")
TILE_DIR = OUT_DIR / "shirt_remix_preview_tiles"
TILE_DIR.mkdir(parents=True, exist_ok=True)
REPORT = OUT_DIR / "shirt_remix_matrix_report.json"
COLLAGE_BASES = OUT_DIR / "shirt_remix_compose_collage.png"
COLLAGE_BODIES = OUT_DIR / "shirt_remix_compose_collage_bodies.png"
COLLAGE_SLEEVES = OUT_DIR / "shirt_remix_compose_collage_sleeves.png"
OVERVIEW = OUT_DIR / "shirt_remix_matrix_overview.png"

BASES = ["C2431027", "C2431055", "C2530790"]
BODIES = {
    "woman": {"sex": "female", "fit": "regular", "ease": 8, "measurements_cm": {"height": 160, "chest": 85, "waist": 66, "shoulder": 38, "neck": 34, "sleeveLength": 52, "upperArm": 26}},
    "man": {"sex": "male_general", "fit": "regular", "ease": 8, "measurements_cm": {"height": 175, "chest": 96, "waist": 82, "shoulder": 45, "neck": 39, "sleeveLength": 60, "upperArm": 32}},
    "elder": {"sex": "female", "fit": "relaxed", "ease": 12, "measurements_cm": {"height": 155, "chest": 98, "waist": 90, "shoulder": 40, "neck": 36, "sleeveLength": 50, "upperArm": 30}},
    "youth": {"sex": "female", "fit": "fitted", "ease": 6, "measurements_cm": {"height": 150, "chest": 78, "waist": 62, "shoulder": 35, "neck": 31, "sleeveLength": 48, "upperArm": 23}},
}
FABRICS = {
    "poplin": "shirt.crisp-formal.poplin",
    "linen": "shirt.natural-casual.linen",
}
STYLES = ["base", "collar", "sleeve", "cuff", "long"]
COLLAR_ALTS = [
    "shirt.collar.pointed",
    "shirt.collar.open-v-pointed",
    "shirt.collar.casual-wide-lapel",
    "shirt.collar.bow-tie",
]
SLEEVE_ALTS = [
    "shirt.sleeve.regular",
    "shirt.sleeve.puff",
    "shirt.sleeve.bell",
    "shirt.sleeve.flutter",
]
CUFF_ALTS = ["shirt.cuff.regular", "shirt.cuff.ruffled", "shirt.cuff.gathered"]
PLACKET_ALTS = ["shirt.placket.full", "shirt.placket.half", "shirt.placket.ruffled"]
SILHOUETTE_ALTS = [
    "shirt.silhouette.a-line",
    "shirt.silhouette.oversized",
    "shirt.silhouette.fitted-x",
    "shirt.silhouette.regular-fit",
    "shirt.silhouette.relaxed-h",
]
SLEEVE_SHOW = ["shirt.sleeve.regular", "shirt.sleeve.puff", "shirt.sleeve.flutter", "shirt.sleeve.batwing"]


def host_body_wh(case_id: str):
    ir = IR_INDEX[case_id]
    ents = _normalize_physical_components(filter_preview_entities(_annotate(ir)))
    return _mean_body_wh(ents)


def pick_alt(group, host_ir, base_opt, candidates):
    donor_index = {cid: ir for cid, ir in IR_INDEX.items() if cid != host_ir.get("case_id")}
    ordered = []
    for opt in candidates:
        if opt == base_opt:
            continue
        rows = rank_donors(group, host_ir, donor_index, max_donors=1, target_option_id=opt)
        if rows:
            ordered.append((rows[0].score, opt, rows[0].case_id))
    ordered.sort(reverse=True)
    return ordered[0] if ordered else None


def base_options(case_id: str) -> dict:
    ir = IR_INDEX[case_id]
    base_opts = {}
    for group, prefix in [
        ("collar", "shirt.collar"),
        ("sleeve", "shirt.sleeve"),
        ("cuff", "shirt.cuff"),
        ("placket", "shirt.placket"),
        ("silhouette", "shirt.silhouette"),
        ("garment_length", "shirt.garment-length"),
    ]:
        slug = part_label_slug(ir, group)
        if slug and slug not in {"unknown", "non_composable"}:
            base_opts[group] = f"{prefix}.{slug}"
    base_opts.setdefault("collar", "shirt.collar.pointed")
    base_opts.setdefault("sleeve", "shirt.sleeve.regular")
    base_opts.setdefault("cuff", "shirt.cuff.regular")
    base_opts.setdefault("placket", "shirt.placket.full")
    base_opts.setdefault("silhouette", "shirt.silhouette.relaxed-h")
    base_opts.setdefault("garment_length", "shirt.garment-length.regular")
    return base_opts


def style_map(case_id: str):
    ir = IR_INDEX[case_id]
    base_opts = base_options(case_id)
    collar = pick_alt("collar", ir, base_opts.get("collar"), COLLAR_ALTS)
    sleeve = pick_alt("sleeve", ir, base_opts.get("sleeve"), SLEEVE_ALTS)
    cuff = pick_alt("cuff", ir, base_opts.get("cuff"), CUFF_ALTS)
    styles = {
        "base": dict(base_opts),
        "collar": {**base_opts, "collar": collar[1]} if collar else dict(base_opts),
        "sleeve": {**base_opts, "sleeve": sleeve[1]} if sleeve else dict(base_opts),
        "cuff": {**base_opts, "cuff": cuff[1]} if cuff else dict(base_opts),
        "long": {**base_opts, "garment_length": "shirt.garment-length.long"},
    }
    meta = {
        "base": {},
        "collar": {"option": collar[1] if collar else None, "donor": collar[2] if collar else None, "score": collar[0] if collar else None},
        "sleeve": {"option": sleeve[1] if sleeve else None, "donor": sleeve[2] if sleeve else None, "score": sleeve[0] if sleeve else None},
        "cuff": {"option": cuff[1] if cuff else None, "donor": cuff[2] if cuff else None, "score": cuff[0] if cuff else None},
        "long": {"option": "shirt.garment-length.long"},
    }
    captions = {
        "base": "base",
        "collar": f"collar {collar[1].split('.')[-1]}\n← {collar[2]}" if collar else "collar",
        "sleeve": f"sleeve {sleeve[1].split('.')[-1]}\n← {sleeve[2]}" if sleeve else "sleeve",
        "cuff": f"cuff {cuff[1].split('.')[-1]}\n← {cuff[2]}" if cuff else "cuff",
        "long": "length +long",
    }
    return base_opts, styles, meta, captions


def check(meta, host_wh):
    """Hard-fail only on missing body / compose crash; geometry gaps are soft for shirt v1."""
    issues = []
    soft = []
    pieces = {p["role"]: p for p in meta.get("pieces") or []}
    f, b = pieces.get("front_body") or {}, pieces.get("back_body") or {}
    if not f:
        f = next((p for r, p in pieces.items() if "front" in r), {}) or {}
    if not b:
        b = next((p for r, p in pieces.items() if "back" in r), {}) or {}
    fw, fh = f.get("width_mm") or 0, f.get("height_mm") or 0
    bw, bh = b.get("width_mm") or 0, b.get("height_mm") or 0
    sleeve = pieces.get("sleeve") or pieces.get("sleeve_left") or pieces.get("sleeve_right") or {}
    sw, sh = sleeve.get("width_mm") or 0, sleeve.get("height_mm") or 0
    sizes = {"front": [round(fw, 1), round(fh, 1)], "back": [round(bw, 1), round(bh, 1)], "sleeve": [round(sw, 1), round(sh, 1)]}
    status = str(meta.get("status") or "")
    if min(fw, fh) <= 0:
        return ["missing_body"], sizes
    if status in {"exception", "error"}:
        issues.append(f"bad_status={status}")
    if bw and fw and abs(fw - bw) / max(fw, bw) > 0.55:
        soft.append(f"fb_width_gap={abs(fw - bw) / max(fw, bw):.2f}")
    if host_wh:
        hw, hh = host_wh
        mw, mh = fw if not bw else (fw + bw) / 2, fh if not bh else (fh + bh) / 2
        if hw and not (0.40 * hw <= mw <= 2.20 * hw):
            soft.append(f"width_vs_host={mw / hw:.2f}")
        if hh and not (0.40 * hh <= mh <= 2.40 * hh):
            soft.append(f"height_vs_host={mh / hh:.2f}")
    # Keep soft issues visible but do not fail the matrix cell.
    return issues + [f"soft:{s}" for s in soft], sizes


def svg_to_png(svg: str, png_path: Path, width: int = 720) -> None:
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        tmp.write(svg.encode("utf-8"))
        svg_path = Path(tmp.name)
    try:
        subprocess.run(
            ["rsvg-convert", "-w", str(width), "-b", "white", str(svg_path), "-o", str(png_path)],
            check=True,
            capture_output=True,
        )
    finally:
        svg_path.unlink(missing_ok=True)


def fonts():
    try:
        return (
            ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 18),
            ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 13),
            ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 22),
        )
    except Exception:
        d = ImageFont.load_default()
        return d, d, d


def stitch(tiles, row_keys, col_keys, title: str, out: Path, cell=(400, 340)):
    font, font_sm, font_title = fonts()
    cell_w, cell_h = cell
    label_h, title_h, pad = 44, 58, 10
    cols, rows_n = len(col_keys), len(row_keys)
    W = pad + cols * (cell_w + pad)
    H = title_h + pad + rows_n * (label_h + cell_h + pad)
    canvas = Image.new("RGB", (W, H), "#f4efe8")
    draw = ImageDraw.Draw(canvas)
    draw.text((pad, 14), title, fill="#3f342f", font=font_title)
    for c, ck in enumerate(col_keys):
        x = pad + c * (cell_w + pad)
        draw.text((x + 8, title_h - 6), str(ck), fill="#7a655c", font=font)
    lookup = {(r, c): t for r, c, t in tiles}
    for r_i, rk in enumerate(row_keys):
        y0 = title_h + pad + r_i * (label_h + cell_h + pad)
        draw.text((pad, y0 + 6), str(rk), fill="#5a463d", font=font)
        for c, ck in enumerate(col_keys):
            item = lookup.get((rk, ck))
            x = pad + c * (cell_w + pad)
            y = y0 + label_h
            draw.rounded_rectangle([x, y, x + cell_w, y + cell_h], radius=10, fill="#ffffff", outline="#e4d7cb")
            if not item:
                draw.text((x + 12, y + 12), "missing", fill="#b04432", font=font_sm)
                continue
            png, caption, status, extra = item
            im = Image.open(png).convert("RGB")
            im.thumbnail((cell_w - 10, cell_h - 48), Image.Resampling.LANCZOS)
            ox = x + (cell_w - im.width) // 2
            oy = y + 8 + max(0, (cell_h - 48 - im.height) // 2)
            canvas.paste(im, (ox, oy))
            draw.multiline_text((x + 8, y + cell_h - 40), caption, fill="#6b5a52", font=font_sm, spacing=1)
            tone = "#2f7d57" if status in {"ok", "valid", "composed"} else "#b04432"
            draw.text((x + cell_w - 64, y + 6), status or "?", fill=tone, font=font_sm)
            if extra:
                draw.text((x + 8, y + 6), extra, fill="#8a7468", font=font_sm)
    canvas.save(out, quality=92)
    print("collage", out.resolve(), canvas.size)


def run_matrix():
    rows = []
    t0 = time.time()
    for base in BASES:
        if base not in IR_INDEX:
            print("skip missing base", base)
            continue
        base_opts, styles, meta_style, _ = style_map(base)
        hwh = host_body_wh(base)
        for style, sel in styles.items():
            for body_name, body in BODIES.items():
                for fab, material in FABRICS.items():
                    recipe = {
                        "family": "shirt",
                        "sex": body["sex"],
                        "base_case_id": base,
                        "measurements_cm": body["measurements_cm"],
                        "fit": body["fit"],
                        "ease_cm": body["ease"],
                        "material_id": material,
                        "fabric_color": "#ffffff",
                        "selections": sel,
                        "base_option_ids": base_opts,
                        "intent_constraints": {},
                        "execution_mode": "shirt_strategy",
                        "compact_layout": True,
                    }
                    t1 = time.time()
                    try:
                        _, meta = compose_shirt(recipe, IR_INDEX, PATTERN_CATALOG)
                        issues, sizes = check(meta, hwh)
                        hard = [i for i in issues if not i.startswith("soft:")]
                        sizing = (meta.get("sources") or {}).get("sizing") or {}
                        rows.append({
                            "base": base,
                            "style": style,
                            "body": body_name,
                            "fabric": fab,
                            "status": meta.get("status"),
                            "pipeline": meta.get("pipeline"),
                            "ms": round((time.time() - t1) * 1000),
                            "ok": len(hard) == 0,
                            "issues": issues,
                            "sizes": sizes,
                            "option": meta_style[style].get("option"),
                            "donor": meta_style[style].get("donor"),
                            "donor_score": meta_style[style].get("score"),
                            "strategies": meta.get("strategies") or {},
                            "body_sx": sizing.get("body_sx"),
                            "body_sy": sizing.get("body_sy"),
                            "front_w": sizes["front"][0],
                            "front_h": sizes["front"][1],
                            "back_w": sizes["back"][0],
                            "back_h": sizes["back"][1],
                            "sleeve_w": sizes["sleeve"][0],
                            "sleeve_h": sizes["sleeve"][1],
                            "host_w": round(hwh[0], 1) if hwh else None,
                            "host_h": round(hwh[1], 1) if hwh else None,
                        })
                    except Exception as e:
                        rows.append({
                            "base": base, "style": style, "body": body_name, "fabric": fab,
                            "ok": False, "issues": [f"exception:{e}"], "status": "exception",
                            "ms": round((time.time() - t1) * 1000),
                        })
                    print(f"matrix {base} {style} {body_name} {fab} ok={rows[-1].get('ok')} status={rows[-1].get('status')}")

    def rate(key):
        bucket = {}
        for r in rows:
            k = r[key]
            bucket.setdefault(k, {"n": 0, "ok": 0})
            bucket[k]["n"] += 1
            bucket[k]["ok"] += int(bool(r.get("ok")))
        for v in bucket.values():
            v["ok_rate"] = round(v["ok"] / v["n"], 4)
        return bucket

    fail_c = Counter(i.split("=")[0] for r in rows if not r.get("ok") for i in (r.get("issues") or []))
    report = {
        "schema": "chi27.shirt-remix-matrix.v1",
        "pipeline": "shirt.strategy_batch.v1",
        "total": len(rows),
        "ok": sum(1 for r in rows if r.get("ok")),
        "fail": sum(1 for r in rows if not r.get("ok")),
        "ok_rate": round(sum(1 for r in rows if r.get("ok")) / max(len(rows), 1), 4),
        "elapsed_s": round(time.time() - t0, 2),
        "axes": {"bases": BASES, "styles": STYLES, "bodies": list(BODIES), "fabrics": list(FABRICS)},
        "by_base": rate("base"),
        "by_style": rate("style"),
        "by_body": rate("body"),
        "by_fabric": rate("fabric"),
        "fail_reasons": dict(fail_c),
        "fail_rows": [r for r in rows if not r.get("ok")],
        "rows": rows,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("total", "ok", "fail", "ok_rate", "elapsed_s", "fail_reasons")}, ensure_ascii=False, indent=2))
    return report


def render_tile(recipe, png: Path):
    ents, meta = compose_shirt(recipe, IR_INDEX, PATTERN_CATALOG)
    svg = svg_for_ir({"atomic_entities": ents})
    svg_to_png(svg, png, width=760)
    strat = meta.get("strategies") or {}
    extra = ",".join(f"{k}:{v.get('mode', '?')}" for k, v in strat.items())[:22]
    return meta.get("status"), extra, meta


def make_visuals():
    tiles = []
    body = BODIES["woman"]
    for base in BASES:
        if base not in IR_INDEX:
            continue
        base_opts, styles, _, captions = style_map(base)
        for style in STYLES:
            recipe = {
                "family": "shirt", "sex": body["sex"], "base_case_id": base,
                "measurements_cm": body["measurements_cm"], "fit": body["fit"], "ease_cm": body["ease"],
                "material_id": FABRICS["poplin"], "fabric_color": "#ffffff",
                "selections": styles[style], "base_option_ids": base_opts,
                "intent_constraints": {}, "execution_mode": "shirt_strategy", "compact_layout": True,
            }
            png = TILE_DIR / f"base_{base}_{style}.png"
            status, extra, _ = render_tile(recipe, png)
            tiles.append((base, style, (png, captions[style], status, extra)))
            print("tile base×style", base, style, status, extra)
    stitch(tiles, [b for b in BASES if b in IR_INDEX], STYLES, "衬衫 · base×组件（女装府绸）", COLLAGE_BASES)

    base = next((b for b in BASES if b in IR_INDEX), None)
    if not base:
        return
    base_opts, styles, _, captions = style_map(base)
    tiles = []
    for body_name, body in BODIES.items():
        for style in STYLES:
            recipe = {
                "family": "shirt", "sex": body["sex"], "base_case_id": base,
                "measurements_cm": body["measurements_cm"], "fit": body["fit"], "ease_cm": body["ease"],
                "material_id": FABRICS["poplin"], "fabric_color": "#ffffff",
                "selections": styles[style], "base_option_ids": base_opts,
                "intent_constraints": {}, "execution_mode": "shirt_strategy", "compact_layout": True,
            }
            png = TILE_DIR / f"body_{body_name}_{style}.png"
            status, extra, _ = render_tile(recipe, png)
            tiles.append((body_name, style, (png, captions[style], status, extra)))
            print("tile body×style", body_name, style, status, extra)
    stitch(tiles, list(BODIES), STYLES, f"衬衫 · 体型×组件（{base} 府绸）", COLLAGE_BODIES)

    tiles = []
    body = BODIES["woman"]
    base_opts = base_options(base)
    for opt in SLEEVE_SHOW:
        sel = {**base_opts, "sleeve": opt}
        recipe = {
            "family": "shirt", "sex": body["sex"], "base_case_id": base,
            "measurements_cm": body["measurements_cm"], "fit": body["fit"], "ease_cm": body["ease"],
            "material_id": FABRICS["poplin"], "fabric_color": "#ffffff",
            "selections": sel, "base_option_ids": base_opts,
            "intent_constraints": {}, "execution_mode": "shirt_strategy", "compact_layout": True,
        }
        slug = opt.split(".")[-1]
        png = TILE_DIR / f"sleeve_{slug}.png"
        status, extra, meta = render_tile(recipe, png)
        mode = ((meta.get("strategies") or {}).get("sleeve") or {}).get("mode") or "—"
        tiles.append((base, slug, (png, f"{slug}\n{mode}", status, extra)))
        print("tile sleeve", slug, status, mode)
    stitch(tiles, [base], [o.split(".")[-1] for o in SLEEVE_SHOW], "衬衫 · 袖策略 puff/flutter/batwing", COLLAGE_SLEEVES, cell=(380, 320))


def make_overview(report: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    fig = plt.figure(figsize=(14, 8), facecolor="#f7f4ef")
    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.28, left=0.08, right=0.97, top=0.88, bottom=0.08)
    fig.suptitle("衬衫排列组合验证（shirt.strategy_batch.v1）", fontsize=18, fontweight="bold", color="#3f342f", y=0.96)
    fig.text(
        0.5, 0.915,
        f"{len(BASES)} base × {len(STYLES)} 风格 × {len(BODIES)} 体型 × {len(FABRICS)} 面料 = {report['total']}  ·  通过 {report['ok']}/{report['total']}（{report['ok_rate']*100:.1f}%）  ·  {report['elapsed_s']}s",
        ha="center", fontsize=11, color="#6b5a52",
    )

    ax = fig.add_subplot(gs[0, 0])
    labels = list(report["by_style"])
    vals = [report["by_style"][k]["ok_rate"] * 100 for k in labels]
    ax.bar(labels, vals, color="#6f8f71")
    ax.set_ylim(0, 105)
    ax.set_title("按风格通过率 %")

    ax = fig.add_subplot(gs[0, 1])
    labels = list(report["by_base"])
    vals = [report["by_base"][k]["ok_rate"] * 100 for k in labels]
    ax.bar(labels, vals, color="#8aa6b5")
    ax.set_ylim(0, 105)
    ax.set_title("按基款通过率 %")
    ax.tick_params(axis="x", rotation=15)

    ax = fig.add_subplot(gs[1, 0])
    labels = list(report["by_body"])
    vals = [report["by_body"][k]["ok_rate"] * 100 for k in labels]
    ax.bar(labels, vals, color="#c4a484")
    ax.set_ylim(0, 105)
    ax.set_title("按体型通过率 %")

    ax = fig.add_subplot(gs[1, 1])
    ax.axis("off")
    ax.text(
        0.02, 0.95,
        f"失败原因: {report.get('fail_reasons') or '无'}\n\n"
        f"拼图:\n· shirt_remix_compose_collage.png\n"
        f"· shirt_remix_compose_collage_bodies.png\n"
        f"· shirt_remix_compose_collage_sleeves.png\n"
        f"· shirt_remix_matrix_report.json",
        va="top", fontsize=12, color="#3f342f",
    )
    fig.savefig(OVERVIEW, dpi=140)
    print("overview", OVERVIEW.resolve())


if __name__ == "__main__":
    report = run_matrix()
    make_visuals()
    make_overview(report)
    print("DONE")
