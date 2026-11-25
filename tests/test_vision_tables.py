"""扫描件表格：坐标 → 网格的三条不变量。

这三条都是"改一行就静默错"的地方，用合成坐标钉住：
  1. 序号列被页码正则吃掉 → 整张表认不出来；
  2. 行归属按"y ≥ 序号" → 下一行首行折行并进上一行；
  3. 表头按容差找列 → 居中的「文件名称」整列丢失。
"""
from __future__ import annotations

from tools.ingest.vision import RE_PAGE_HARD, _page_grid, looks_tabular


def _obs(text, x, y, h=0.015, page=1):
    return {"page": page, "text": text, "x": x, "y": y, "h": h}


def _row(n, y, name):
    return [_obs(str(n), 0.14, y + 0.004), _obs(name, 0.19, y - 0.004),
            _obs(f"沪人社规〔2021〕{n}号", 0.61, y), _obs("延长至2028年", 0.73, y)]


def test_grid_uses_seq_anchor_and_keeps_wrapped_lines():
    obs = [_obs("序号", 0.13, 0.12), _obs("文件名称", 0.35, 0.121),
           _obs("文号", 0.64, 0.12), _obs("有效期", 0.75, 0.12)]
    for i, y in enumerate((0.15, 0.19, 0.23), start=1):
        obs += _row(i, y, "上海市劳动和社会保障局关于本市企业职工")
        obs += [_obs("疾病休假工资或疾病救济费最低标准的通知", 0.19, y + 0.012)]
    grid = _page_grid(obs)
    assert grid is not None
    assert len(grid) == 4                      # 表头 + 3 行，折行不另起一行
    assert grid[0][:2] == ["序号", "文件名称"]  # 居中表头也归位
    assert grid[1][0] == "1"
    assert grid[1][1] == "上海市劳动和社会保障局关于本市企业职工疾病休假工资或疾病救济费最低标准的通知"


def test_grid_rejects_single_column_page():
    obs = [_obs("上海市人力资源和社会保障局", 0.2, y) for y in (0.1, 0.13, 0.16, 0.19, 0.22)]
    assert _page_grid(obs) is None


def test_page_number_regex_needs_dashes():
    assert RE_PAGE_HARD.match("— 2 —")
    assert not RE_PAGE_HARD.match("2")      # 序号不能被当页码


def test_looks_tabular_detects_cellish_lines():
    lines = ["序号", "1", "关于…的通知", "沪人社规〔2021〕6号", "延长至2028年"] * 3
    assert looks_tabular(lines)
    assert not looks_tabular(["这是一段完整的正文，句子很长并且以句号结尾。" * 5])
