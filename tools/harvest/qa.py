"""抓上海市人社局的「政策问答」栏目，把官方的问答对变成评测题的候选池。

为什么用这个源：题目不是我们出的，答案里有官方文号，问题写法接近普通人。
（见 docs/eval-datasets-research.md：公开中文政务 QA 集基本不存在。）

产物：data/harvest/qa/*.json，每篇一个文件；data/harvest/index.jsonl 汇总。
断点续跑：已存在的文件跳过。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE = "https://rsj.sh.gov.cn"
COLUMN = "/tzcjd_17352_17352"
OUT = Path("data/harvest/qa")
UA = {"User-Agent": "ClauseLedger/0.1"}  # 长 UA 会被 WAF 403，实测

# 文号：沪人社规〔2026〕18号 / 沪劳保发（90）109号 / 沪人社就发〔2016〕28 号
DOCNO = re.compile(r"沪[^\s，。；：、（）()《》]{0,14}?[〔（(]\s*\d{2,4}\s*[〕）)]\s*第?\s*\d+\s*号")
QNUM = re.compile(r"^(\d{1,2})[.、．]\s*(.+?[？?])\s*$")


def get(url: str) -> str:
    for i in range(3):
        try:
            r = httpx.get(url, headers=UA, timeout=25, follow_redirects=True)
            if r.status_code == 200:
                return r.content.decode("utf-8", errors="ignore")
        except httpx.HTTPError:
            pass
        time.sleep(1.5 * (i + 1))
    return ""


def list_articles() -> list[dict]:
    out, seen = [], set()
    for page in range(1, 30):
        url = f"{BASE}{COLUMN}/index{'' if page == 1 else f'_{page}'}.html"
        html = get(url)
        if not html:
            break
        soup = BeautifulSoup(html, "lxml")
        arts = [a for a in soup.find_all("a", href=True)
                if re.search(r"/t\d{4}_\d+\.html$", a["href"])]
        if not arts:
            break
        new = 0
        for a in arts:
            href = a["href"]
            if href in seen:
                continue
            seen.add(href)
            out.append({"url": BASE + href, "title": a.get_text(strip=True)})
            new += 1
        print(f"  列表第{page}页 +{new}", file=sys.stderr)
        if new == 0:
            break
        time.sleep(0.8)
    return out


def split_qa(text: str) -> list[dict]:
    """把正文切成问答对：题号行以问号结尾，答案从「答：」开始到下个题号为止。"""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    pairs, cur = [], None
    for line in lines:
        m = QNUM.match(line)
        if m:
            if cur:
                pairs.append(cur)
            cur = {"q": m.group(2), "a_lines": []}
        elif cur is not None:
            if line.startswith(("答：", "答:")):
                line = line[2:].strip()
            cur["a_lines"].append(line)
    if cur:
        pairs.append(cur)
    return [{"q": p["q"], "a": "".join(p["a_lines"]).strip()} for p in pairs if p["a_lines"]]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    arts = list_articles()
    print(f"共 {len(arts)} 篇", file=sys.stderr)
    idx = []
    for i, a in enumerate(arts, 1):
        name = a["url"].rsplit("/", 1)[-1].replace(".html", "")
        path = OUT / f"{name}.json"
        if path.exists():
            idx.append(json.loads(path.read_text("utf-8")))
            continue
        html = get(a["url"])
        if not html:
            print(f"  [{i}] 失败 {a['url']}", file=sys.stderr)
            continue
        soup = BeautifulSoup(html, "lxml")
        title = (soup.find("h1") or soup.find("title"))
        title = title.get_text(strip=True) if title else a["title"]
        title = title.split("_政策解读")[0]
        body = soup.get_text("\n", strip=True)
        m = re.search(r"发布时间：\s*(\d{4}-\d{2}-\d{2})", body)
        pairs = split_qa(body)
        for p in pairs:
            p["docnos"] = sorted(set(DOCNO.findall(p["q"] + " " + p["a"])))
        rec = {"url": a["url"], "title": title,
               "date": m.group(1) if m else "", "qa": pairs}
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")
        idx.append({"url": a["url"], "title": title, "date": rec["date"], "n_qa": len(pairs)})
        print(f"  [{i}/{len(arts)}] {len(pairs)} 组  {title[:30]}", file=sys.stderr)
        time.sleep(0.9)
    Path("data/harvest/index.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in idx), "utf-8")
    total = sum(r["n_qa"] for r in idx)
    print(f"完成：{len(idx)} 篇，{total} 组问答", file=sys.stderr)


if __name__ == "__main__":
    main()
