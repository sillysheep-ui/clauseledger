"""画判据的决策边界：score = 0.40·lex + 0.25·margin + 0.35·cos >= 0.38。

x = 语义余弦（原始值 0.45–0.95），y = 分差项 min(分差/4, 1)。
三条等值线对应 lex = 0.4 / 0.6 / 0.8（top-1 词面分 4.8 / 7.2 / 9.6）。
点：dev 答对的题、答错的题、域内无政策题。

输出 docs/shots/gate-boundary.svg（再由 Playwright 转 PNG）。
"""
from __future__ import annotations

from pathlib import Path

from app.retrieval import confidence as C
from tools.gate_ablation import questions, record

W, H = 760, 600
L, R, T, B = 76, 24, 28, 88
X0, X1 = 0.45, 0.95
Y0, Y1 = 0.0, 1.0


def px(cos: float) -> float:
    return L + (cos - X0) / (X1 - X0) * (W - L - R)


def py(m: float) -> float:
    return H - B - (m - Y0) / (Y1 - Y0) * (H - T - B)


def contour(lex: float, t: float = 0.38) -> list[tuple[float, float]]:
    pts = []
    for i in range(201):
        cos = X0 + (X1 - X0) * i / 200
        m = (t - 0.40 * lex - 0.35 * cos) / 0.25
        if 0 <= m <= 1:
            pts.append((cos, m))
    return pts


def main() -> None:
    dev, miss, oos = questions()
    rd = record(dev)
    rm = record(miss)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="-apple-system, PingFang SC, sans-serif">',
             f'<rect width="{W}" height="{H}" fill="#fff"/>']

    # 网格
    for c in (0.5, 0.6, 0.7, 0.8, 0.9):
        parts.append(f'<line x1="{px(c):.1f}" y1="{T}" x2="{px(c):.1f}" y2="{H-B}" stroke="#eceff3"/>')
        parts.append(f'<text x="{px(c):.1f}" y="{H-B+20}" font-size="12" fill="#6b7280" '
                     f'text-anchor="middle">{c:.1f}</text>')
    for m in (0.25, 0.5, 0.75, 1.0):
        parts.append(f'<line x1="{L}" y1="{py(m):.1f}" x2="{W-R}" y2="{py(m):.1f}" stroke="#eceff3"/>')
        parts.append(f'<text x="{L-10}" y="{py(m)+4:.1f}" font-size="12" fill="#6b7280" '
                     f'text-anchor="end">{m:.2f}</text>')

    # 等值线
    for lex, color, dash in ((0.0, "#c9b8a8", "6 5"), (0.2, "#a08b76", "5 4"), (0.4, "#6f5a45", "")):
        pts = contour(lex)
        if not pts:
            continue
        d = " ".join(("M" if i == 0 else "L") + f"{px(c):.1f},{py(m):.1f}"
                     for i, (c, m) in enumerate(pts))
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2" '
                     f'{("stroke-dasharray=" + chr(34) + dash + chr(34)) if dash else ""}/>')
        c, m = pts[0]
        parts.append(f'<text x="{px(c)+10:.1f}" y="{py(m)-10:.1f}" font-size="11" fill="{color}" '
                     f'stroke="#fff" stroke-width="3" paint-order="stroke">lex={lex:.1f}</text>')

    # 判据区域标注
    parts.append(f'<text x="{px(0.90):.1f}" y="{py(0.60):.1f}" font-size="13" fill="#2f6f4f" '
                 f'text-anchor="end">线以上：答</text>')
    parts.append(f'<text x="{px(0.47):.1f}" y="{py(0.10):.1f}" font-size="13" fill="#9aa3af">'
                 f'线以下：拒答</text>')

    # 点
    for r, g in zip(rd, [q.get("gold_doc") for q in dev]):
        if r["guarded"] or r["cos"] is None:
            continue
        m = min(max(r["margin"], 0) / 4, 1)
        ok = r["doc"] == g
        color = "#2f6f4f" if ok else "#c47a2c"
        parts.append(f'<circle cx="{px(r["cos"]):.1f}" cy="{py(m):.1f}" r="3.4" '
                     f'fill="{color}" fill-opacity="0.72"/>')
    for r in rm:
        if r["guarded"] or r["cos"] is None:
            continue
        m = min(max(r["margin"], 0) / 4, 1)
        answered = C.score(r["score"], r["margin"], r["cos"]) >= 0.38
        if answered:
            parts.append(f'<circle cx="{px(r["cos"]):.1f}" cy="{py(m):.1f}" r="5" fill="none" '
                         f'stroke="#c0392b" stroke-width="2"/>')
        else:
            parts.append(f'<circle cx="{px(r["cos"]):.1f}" cy="{py(m):.1f}" r="3.4" fill="none" '
                         f'stroke="#9aa3af" stroke-width="1.4"/>')

    # 轴标题
    parts.append(f'<text x="{(L+W-R)/2:.0f}" y="{H-B+46}" font-size="13" fill="#374151" '
                 f'text-anchor="middle">语义余弦（top-1 条款）</text>')
    parts.append(f'<text x="18" y="{(T+H-B)/2:.0f}" font-size="13" fill="#374151" '
                 f'text-anchor="middle" transform="rotate(-90 18 {(T+H-B)/2:.0f})">'
                 f'分差项 min(分差/4, 1)</text>')

    # 图例：横排放在坐标区外，避免压住点
    lx, ly = L, H - 12
    items = [("#2f6f4f", "dev：答对"), ("#c47a2c", "dev：答错"),
             ("#c0392b", "无政策：被误答"), ("#9aa3af", "无政策：正确拒答"),
             ("#8a7159", "虚线 = 判据等值线")]
    for c, label in items:
        if c == "#8a7159":
            parts.append(f'<line x1="{lx}" y1="{ly-4}" x2="{lx+12}" y2="{ly-4}" stroke="{c}" '
                         f'stroke-width="2" stroke-dasharray="4 3"/>')
        elif c in ("#9aa3af", "#c0392b"):
            parts.append(f'<circle cx="{lx+6}" cy="{ly-4}" r="4.5" fill="none" stroke="{c}" stroke-width="1.8"/>')
        else:
            parts.append(f'<circle cx="{lx+6}" cy="{ly-4}" r="4.5" fill="{c}" fill-opacity="0.8"/>')
        parts.append(f'<text x="{lx+16}" y="{ly}" font-size="12" fill="#374151">{label}</text>')
        lx += 26 + len(label) * 12

    parts.append("</svg>")
    out = Path("docs/shots/gate-boundary.svg")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), "utf-8")
    print("wrote", out, "points", len(rd), len(rm))


if __name__ == "__main__":
    main()
