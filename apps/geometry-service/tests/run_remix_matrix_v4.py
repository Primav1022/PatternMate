"""Re-run remix matrix + visual collages with sleeve-cap↔armhole morph."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app import IR_INDEX, PATTERN_CATALOG, svg_for_ir
from composition_engine import FRONT_ROLES, BACK_ROLES, filter_preview_entities, _normalize_physical_components
from donor_similarity import part_label_slug, rank_donors
from simple_compose import compose_simple, _annotate, _mean_body_wh, _role

ROOT = Path(__file__).resolve().parents[1] if False else Path(".")
# script run from patternmate/
OUT_DIR = Path("apps/geometry-service/tests")
TILE_DIR = OUT_DIR / "remix_preview_tiles_v4"
TILE_DIR.mkdir(parents=True, exist_ok=True)
REPORT = OUT_DIR / "minimal_remix_matrix_report.json"
COLLAGE_BASES = OUT_DIR / "remix_compose_collage.png"
COLLAGE_BODIES = OUT_DIR / "remix_compose_collage_bodies.png"
COLLAGE_SLEEVES = OUT_DIR / "remix_compose_collage_sleeves.png"
OVERVIEW = OUT_DIR / "minimal_remix_matrix_overview.png"

BASES = ["C2590529", "C2490278"]  # C2490257 quarantined: jagged/unusable host geometry
BODIES = {
    "woman": {"sex": "female", "fit": "regular", "ease": 8, "measurements_cm": {"height": 160, "chest": 85, "waist": 66, "shoulder": 38, "neck": 34, "sleeveLength": 52, "upperArm": 26}},
    "man": {"sex": "male_general", "fit": "regular", "ease": 8, "measurements_cm": {"height": 175, "chest": 96, "waist": 82, "shoulder": 45, "neck": 39, "sleeveLength": 60, "upperArm": 32}},
    "elder": {"sex": "female", "fit": "relaxed", "ease": 12, "measurements_cm": {"height": 155, "chest": 98, "waist": 90, "shoulder": 40, "neck": 36, "sleeveLength": 50, "upperArm": 30}},
    "youth": {"sex": "female", "fit": "fitted", "ease": 6, "measurements_cm": {"height": 150, "chest": 78, "waist": 62, "shoulder": 35, "neck": 31, "sleeveLength": 48, "upperArm": 23}},
}
FABRICS = {
    "cotton": "tshirt.soft-basic.cotton-jersey",
    "tencel": "tshirt.soft-basic.tencel-cotton",
}
STYLES = ["base", "neck", "sleeve", "long"]
NECK_ALTS = ["tshirt.neckline.v-neck", "tshirt.neckline.crew", "tshirt.neckline.boat", "tshirt.neckline.high-mock"]
SLEEVE_ALTS = ["tshirt.sleeve.puff", "tshirt.sleeve.flutter", "tshirt.sleeve.raglan", "tshirt.sleeve.set-in"]
SLEEVE_SHOW = ["tshirt.sleeve.set-in", "tshirt.sleeve.puff", "tshirt.sleeve.raglan", "tshirt.sleeve.flutter"]


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
    for group, prefix in [("neckline", "tshirt.neckline"), ("sleeve", "tshirt.sleeve"), ("garment_length", "tshirt.garment-length")]:
        slug = part_label_slug(ir, group)
        if slug:
            base_opts[group] = f"{prefix}.{slug}"
    base_opts.setdefault("neckline", "tshirt.neckline.crew")
    base_opts.setdefault("sleeve", "tshirt.sleeve.set-in")
    base_opts.setdefault("garment_length", "tshirt.garment-length.regular")
    return base_opts


def style_map(case_id: str):
    ir = IR_INDEX[case_id]
    base_opts = base_options(case_id)
    neck = pick_alt("neckline", ir, base_opts.get("neckline"), NECK_ALTS)
    sleeve = pick_alt("sleeve", ir, base_opts.get("sleeve"), SLEEVE_ALTS)
    styles = {
        "base": dict(base_opts),
        "neck": {**base_opts, "neckline": neck[1]} if neck else dict(base_opts),
        "sleeve": {**base_opts, "sleeve": sleeve[1]} if sleeve else dict(base_opts),
        "long": {**base_opts, "garment_length": "tshirt.garment-length.long"},
    }
    meta = {
        "base": {},
        "neck": {"option": neck[1] if neck else None, "donor": neck[2] if neck else None, "score": neck[0] if neck else None},
        "sleeve": {"option": sleeve[1] if sleeve else None, "donor": sleeve[2] if sleeve else None, "score": sleeve[0] if sleeve else None},
        "long": {"option": "tshirt.garment-length.long"},
    }
    captions = {
        "base": "base",
        "neck": f"neck {neck[1].split('.')[-1]}\n← {neck[2]}" if neck else "neck",
        "sleeve": f"sleeve {sleeve[1].split('.')[-1]}\n← {sleeve[2]}" if sleeve else "sleeve",
        "long": "length +10%",
    }
    return base_opts, styles, meta, captions


def check(meta, host_wh):
    issues = []
    pieces = {p["role"]: p for p in meta.get("pieces") or []}
    f, b = pieces.get("front_body") or {}, pieces.get("back_body") or {}
    fw, fh = f.get("width_mm") or 0, f.get("height_mm") or 0
    bw, bh = b.get("width_mm") or 0, b.get("height_mm") or 0
    sw, sh = (pieces.get("sleeve") or {}).get("width_mm") or 0, (pieces.get("sleeve") or {}).get("height_mm") or 0
    sizes = {"front": [round(fw, 1), round(fh, 1)], "back": [round(bw, 1), round(bh, 1)], "sleeve": [round(sw, 1), round(sh, 1)]}
    if min(fw, fh, bw, bh) <= 0:
        return ["missing_body"], sizes
    if abs(fw - bw) / max(fw, bw) > 0.20:
        issues.append(f"fb_width_gap={abs(fw - bw) / max(fw, bw):.2f}")
    if abs(fh - bh) / max(fh, bh) > 0.25:
        issues.append(f"fb_height_gap={abs(fh - bh) / max(fh, bh):.2f}")
    if host_wh:
        hw, hh = host_wh
        mw, mh = (fw + bw) / 2, (fh + bh) / 2
        if not (0.72 * hw <= mw <= 1.50 * hw):
            issues.append(f"width_vs_host={mw / hw:.2f}")
        if not (0.68 * hh <= mh <= 1.60 * hh):
            issues.append(f"height_vs_host={mh / hh:.2f}")
    aspect = fh / max(fw, 1)
    if aspect < 0.55 or aspect > 2.4:
        issues.append(f"front_aspect={aspect:.2f}")
    if sw and fw and sw > fw * 3.8:
        issues.append("sleeve_too_large")
    return issues, sizes


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
            tone = "#2f7d57" if status == "valid" else "#b04432"
            draw.text((x + cell_w - 64, y + 6), status or "?", fill=tone, font=font_sm)
            if extra:
                draw.text((x + 8, y + 6), extra, fill="#8a7468", font=font_sm)
    canvas.save(out, quality=92)
    print("collage", out.resolve(), canvas.size)


def run_matrix():
    rows = []
    t0 = time.time()
    for base in BASES:
        base_opts, styles, meta_style, _ = style_map(base)
        hwh = host_body_wh(base)
        for style, sel in styles.items():
            for body_name, body in BODIES.items():
                for fab, material in FABRICS.items():
                    recipe = {
                        "family": "tshirt",
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
                        "execution_mode": "simple_piece_swap",
                        "compact_layout": True,
                    }
                    t1 = time.time()
                    try:
                        _, meta = compose_simple(recipe, IR_INDEX, PATTERN_CATALOG)
                        issues, sizes = check(meta, hwh)
                        sizing = (meta.get("sources") or {}).get("sizing") or {}
                        cap = (meta.get("sources") or {}).get("sleeve_cap_match") or {}
                        rows.append({
                            "base": base,
                            "style": style,
                            "body": body_name,
                            "fabric": fab,
                            "status": meta.get("status"),
                            "ms": round((time.time() - t1) * 1000),
                            "ok": len(issues) == 0,
                            "issues": issues,
                            "sizes": sizes,
                            "option": meta_style[style].get("option"),
                            "donor": meta_style[style].get("donor"),
                            "donor_score": meta_style[style].get("score"),
                            "body_sx": sizing.get("body_sx"),
                            "body_sy": sizing.get("body_sy"),
                            "shrink": (meta.get("sizing_profile") or {}).get("material_shrink_rate"),
                            "front_w": sizes["front"][0],
                            "front_h": sizes["front"][1],
                            "back_w": sizes["back"][0],
                            "back_h": sizes["back"][1],
                            "sleeve_w": sizes["sleeve"][0],
                            "sleeve_h": sizes["sleeve"][1],
                            "host_w": round(hwh[0], 1) if hwh else None,
                            "host_h": round(hwh[1], 1) if hwh else None,
                            "cap_applied": bool(cap.get("applied")),
                            "cap_err": cap.get("max_abs_error_ratio"),
                            "cap_ease": cap.get("ease"),
                            "cap_ah": cap.get("body_armhole"),
                        })
                    except Exception as e:
                        rows.append({
                            "base": base, "style": style, "body": body_name, "fabric": fab,
                            "ok": False, "issues": [f"exception:{e}"], "status": "exception",
                            "ms": round((time.time() - t1) * 1000),
                        })
                    print(f"matrix {base} {style} {body_name} {fab} ok={rows[-1].get('ok')} cap={rows[-1].get('cap_applied')}")

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

    grade_ok = grade_n = 0
    by_key = {}
    for r in rows:
        if not r.get("ok"):
            continue
        by_key.setdefault((r["base"], r["style"], r["body"]), {})[r["fabric"]] = r
    for pair in by_key.values():
        if "cotton" in pair and "tencel" in pair:
            grade_n += 1
            if pair["tencel"]["front_w"] + 0.5 >= pair["cotton"]["front_w"]:
                grade_ok += 1

    body_cmp_n = body_cmp_ok = 0
    by2 = {}
    for r in rows:
        if not r.get("ok"):
            continue
        by2.setdefault((r["base"], r["style"], r["fabric"]), {})[r["body"]] = r
    for d in by2.values():
        if "elder" in d and "youth" in d:
            body_cmp_n += 1
            if d["elder"]["front_w"] >= d["youth"]["front_w"] - 1:
                body_cmp_ok += 1

    long_n = long_ok = 0
    by3 = {}
    for r in rows:
        if not r.get("ok"):
            continue
        by3.setdefault((r["base"], r["body"], r["fabric"]), {})[r["style"]] = r
    for d in by3.values():
        if "base" in d and "long" in d and d["base"]["front_h"]:
            long_n += 1
            ratio = d["long"]["front_h"] / d["base"]["front_h"]
            if 1.05 <= ratio <= 1.16:
                long_ok += 1

    cap_rows = [r for r in rows if r.get("style") == "sleeve" and r.get("ok")]
    fail_c = Counter(i.split("=")[0] for r in rows if not r.get("ok") for i in (r.get("issues") or []))
    report = {
        "schema": "chi27.minimal-remix-matrix.v4-sleeve-cap",
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
        "grading_checks": {
            "tencel_ge_cotton_width": {"n": grade_n, "ok": grade_ok, "ok_rate": round(grade_ok / max(grade_n, 1), 4)},
            "elder_ge_youth_width": {"n": body_cmp_n, "ok": body_cmp_ok, "ok_rate": round(body_cmp_ok / max(body_cmp_n, 1), 4)},
            "long_ge_base_height": {"n": long_n, "ok": long_ok, "ok_rate": round(long_ok / max(long_n, 1), 4)},
        },
        "sleeve_cap_match": {
            "sleeve_style_rows": len(cap_rows),
            "applied": sum(1 for r in cap_rows if r.get("cap_applied")),
            "mean_err": round(sum(abs(r.get("cap_err") or 0) for r in cap_rows if r.get("cap_applied")) / max(sum(1 for r in cap_rows if r.get("cap_applied")), 1), 4),
        },
        "fail_reasons": dict(fail_c),
        "fail_rows": [r for r in rows if not r.get("ok")],
        "rows": rows,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("total", "ok", "fail", "ok_rate", "elapsed_s", "grading_checks", "sleeve_cap_match", "fail_reasons")}, ensure_ascii=False, indent=2))
    return report


def render_tile(recipe, png: Path):
    ents, meta = compose_simple(recipe, IR_INDEX, PATTERN_CATALOG)
    svg = svg_for_ir({"atomic_entities": ents})
    svg_to_png(svg, png, width=760)
    cap = (meta.get("sources") or {}).get("sleeve_cap_match") or {}
    extra = ""
    if cap.get("applied"):
        extra = f"cap±{cap.get('max_abs_error_ratio')}"
    elif cap.get("reason"):
        extra = str(cap.get("reason"))[:18]
    return meta.get("status"), extra, cap


def make_visuals():
    # 1) bases × styles (woman cotton)
    tiles = []
    body = BODIES["woman"]
    for base in BASES:
        base_opts, styles, _, captions = style_map(base)
        for style in STYLES:
            recipe = {
                "family": "tshirt", "sex": body["sex"], "base_case_id": base,
                "measurements_cm": body["measurements_cm"], "fit": body["fit"], "ease_cm": body["ease"],
                "material_id": FABRICS["cotton"], "fabric_color": "#ffffff",
                "selections": styles[style], "base_option_ids": base_opts,
                "intent_constraints": {}, "execution_mode": "simple_piece_swap", "compact_layout": True,
            }
            png = TILE_DIR / f"base_{base}_{style}.png"
            status, extra, _ = render_tile(recipe, png)
            tiles.append((base, style, (png, captions[style], status, extra)))
            print("tile base×style", base, style, status, extra)
    stitch(tiles, BASES, STYLES, "新算法 · base×组件（女装棉） 原样/换领/换袖/加长", COLLAGE_BASES)

    # 2) bodies × styles on C2590529 cotton
    base = "C2590529"
    base_opts, styles, _, captions = style_map(base)
    tiles = []
    for body_name, body in BODIES.items():
        for style in STYLES:
            recipe = {
                "family": "tshirt", "sex": body["sex"], "base_case_id": base,
                "measurements_cm": body["measurements_cm"], "fit": body["fit"], "ease_cm": body["ease"],
                "material_id": FABRICS["cotton"], "fabric_color": "#ffffff",
                "selections": styles[style], "base_option_ids": base_opts,
                "intent_constraints": {}, "execution_mode": "simple_piece_swap", "compact_layout": True,
            }
            png = TILE_DIR / f"body_{body_name}_{style}.png"
            status, extra, _ = render_tile(recipe, png)
            tiles.append((body_name, style, (png, captions[style], status, extra)))
            print("tile body×style", body_name, style, status, extra)
    stitch(tiles, list(BODIES), STYLES, "新算法 · 体型/年龄×组件（C2590529 棉） woman/man/elder/youth", COLLAGE_BODIES)

    # 3) sleeve types on C2590529 woman
    tiles = []
    body = BODIES["woman"]
    base_opts = base_options(base)
    for opt in SLEEVE_SHOW:
        sel = {**base_opts, "sleeve": opt}
        recipe = {
            "family": "tshirt", "sex": body["sex"], "base_case_id": base,
            "measurements_cm": body["measurements_cm"], "fit": body["fit"], "ease_cm": body["ease"],
            "material_id": FABRICS["cotton"], "fabric_color": "#ffffff",
            "selections": sel, "base_option_ids": base_opts,
            "intent_constraints": {}, "execution_mode": "simple_piece_swap", "compact_layout": True,
        }
        slug = opt.split(".")[-1]
        png = TILE_DIR / f"sleeve_{slug}.png"
        status, extra, cap = render_tile(recipe, png)
        cap_txt = f"ease {cap.get('ease')} err {cap.get('max_abs_error_ratio')}" if cap.get("applied") else (cap.get("reason") or "no-cap")
        tiles.append(("C2590529", slug, (png, f"{slug}\n{cap_txt}", status, extra)))
        print("tile sleeve", slug, status, cap_txt)
    stitch(tiles, ["C2590529"], [o.split(".")[-1] for o in SLEEVE_SHOW], "新算法 · 袖型迁移（袖山↔袖窿） set-in/puff/raglan/flutter", COLLAGE_SLEEVES, cell=(380, 320))


def make_overview(report: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    rows = report["rows"]
    fig = plt.figure(figsize=(14, 9), facecolor="#f7f4ef")
    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.28, left=0.08, right=0.97, top=0.88, bottom=0.08)
    fig.suptitle("T恤排列组合验证（袖山匹配 v4）", fontsize=18, fontweight="bold", color="#3f342f", y=0.96)
    fig.text(
        0.5, 0.915,
        f"3 base × 4 风格 × 4 体型 × 2 面料 = {report['total']}  ·  通过 {report['ok']}/{report['total']}（{report['ok_rate']*100:.1f}%）  ·  {report['elapsed_s']}s",
        ha="center", fontsize=11, color="#6b5a52",
    )

    ax = fig.add_subplot(gs[0, 0])
    labels = list(report["by_body"])
    vals = [report["by_body"][k]["ok_rate"] * 100 for k in labels]
    ax.bar(labels, vals, color="#6f8f71")
    ax.set_ylim(0, 105)
    ax.set_title("按体型通过率 %")
    ax.set_ylabel("%")

    ax = fig.add_subplot(gs[0, 1])
    body_sizes = [x for x in rows if x.get("base") == "C2590529" and x.get("style") == "base" and x.get("ok")]
    bodies = list(BODIES)
    cotton = [next(x["front_w"] for x in body_sizes if x["body"] == b and x["fabric"] == "cotton") for b in bodies]
    tencel = [next(x["front_w"] for x in body_sizes if x["body"] == b and x["fabric"] == "tencel") for b in bodies]
    x = range(len(bodies))
    ax.bar([i - 0.18 for i in x], cotton, width=0.36, label="cotton", color="#c4a484")
    ax.bar([i + 0.18 for i in x], tencel, width=0.36, label="tencel", color="#8aa6b5")
    ax.set_xticks(list(x), bodies)
    ax.set_title("C2590529 base 前片宽（体型×面料）")
    ax.legend()

    ax = fig.add_subplot(gs[1, 0])
    g = report["grading_checks"]
    names = ["天丝≥棉宽", "老人≥少年宽", "加长≈×1.10"]
    rates = [g["tencel_ge_cotton_width"]["ok_rate"] * 100, g["elder_ge_youth_width"]["ok_rate"] * 100, g["long_ge_base_height"]["ok_rate"] * 100]
    ax.barh(names, rates, color="#7a9e9f")
    ax.set_xlim(0, 105)
    ax.set_title("放码检查通过率 %")

    ax = fig.add_subplot(gs[1, 1])
    cap = report.get("sleeve_cap_match") or {}
    ax.axis("off")
    txt = (
        f"袖山匹配（style=sleeve 行）\n"
        f"applied {cap.get('applied')}/{cap.get('sleeve_style_rows')}\n"
        f"mean |err| {cap.get('mean_err')}\n\n"
        f"失败原因: {report.get('fail_reasons') or '无'}\n"
        f"拼图:\n"
        f"· remix_compose_collage.png\n"
        f"· remix_compose_collage_bodies.png\n"
        f"· remix_compose_collage_sleeves.png"
    )
    ax.text(0.02, 0.95, txt, va="top", fontsize=12, family="sans-serif", color="#3f342f")
    fig.savefig(OVERVIEW, dpi=140)
    print("overview", OVERVIEW.resolve())


if __name__ == "__main__":
    report = run_matrix()
    make_visuals()
    make_overview(report)
    print("DONE")
