"""把 Markdown 发到飞书云文档。

两条路，优先第一条：
  A. 导入任务：把 .md 原样上传，让飞书自己转成 docx —— 格式最完整，代码块/表格都保得住。
  B. 块追加：建空文档再按块写 —— 只用于 A 不可用时，标题/正文/列表能保，表格会降级成文本行。

凭据从 .env 读（不进 git）：
  FEISHU_APP_ID / FEISHU_APP_SECRET
应用需要两个权限：docx:document（读写文档）、drive:drive（上传文件）。
权限加完必须**发布版本**，否则 token 里没有 scope，会报 99991672。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = "https://open.feishu.cn/open-apis"
SCOPE_HINT = ("https://open.feishu.cn/app/{app_id}/auth?q=docx:document:create,drive:drive")


def load_env(path: Path = Path(".env")) -> dict[str, str]:
    env = dict(os.environ)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def token(env: dict[str, str]) -> str:
    r = httpx.post(f"{BASE}/auth/v3/tenant_access_token/internal",
                   json={"app_id": env["FEISHU_APP_ID"],
                         "app_secret": env["FEISHU_APP_SECRET"]}, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        raise SystemExit(f"拿 token 失败：{d.get('code')} {d.get('msg')}")
    return d["tenant_access_token"]


def _denied(d: dict, env: dict[str, str]) -> None:
    """权限不足时给出可执行的下一步，而不是一串错误码。"""
    if d.get("code") == 99991672:
        raise SystemExit(
            "应用还没有文档权限。去这里加 docx:document 和 drive:drive，然后**发布版本**：\n  "
            + SCOPE_HINT.format(app_id=env["FEISHU_APP_ID"]))


def import_markdown(md: Path, title: str, tk: str, env: dict[str, str]) -> dict:
    h = {"Authorization": f"Bearer {tk}"}
    data = md.read_bytes()
    up = httpx.post(f"{BASE}/drive/v1/files/upload_all", headers=h, timeout=120,
                    data={"file_name": md.name, "parent_type": "explorer",
                          "parent_node": "", "size": str(len(data))},
                    files={"file": (md.name, data, "text/markdown")})
    u = up.json()
    _denied(u, env)
    if u.get("code") != 0:
        raise RuntimeError(f"上传失败：{u.get('code')} {u.get('msg')}")
    ft = u["data"]["file_token"]

    task = httpx.post(f"{BASE}/drive/v1/import_tasks", headers=h, timeout=60,
                      json={"file_extension": "md", "file_token": ft, "type": "docx",
                            "file_name": title, "point": {"mount_type": 1, "mount_key": ""}})
    t = task.json()
    _denied(t, env)
    if t.get("code") != 0:
        raise RuntimeError(f"建导入任务失败：{t.get('code')} {t.get('msg')}")
    ticket = t["data"]["ticket"]

    for _ in range(60):
        time.sleep(1.5)
        g = httpx.get(f"{BASE}/drive/v1/import_tasks/{ticket}", headers=h, timeout=30).json()
        st = (g.get("data") or {}).get("result", {}).get("job_status")
        if st == 0:
            return g["data"]["result"]
        if st not in (None, 1, 2):
            raise RuntimeError(f"导入失败 job_status={st}: {json.dumps(g, ensure_ascii=False)[:300]}")
    raise RuntimeError("导入超时")


def create_doc(title: str, tk: str, env: dict[str, str]) -> tuple[str, str]:
    h = {"Authorization": f"Bearer {tk}"}
    r = httpx.post(f"{BASE}/docx/v1/documents", headers=h, json={"title": title}, timeout=30)
    d = r.json()
    _denied(d, env)
    if d.get("code") != 0:
        raise RuntimeError(f"建文档失败：{d.get('code')} {d.get('msg')}")
    return d["data"]["document"]["document_id"], d["data"]["document"]["revision_id"]


def _run(text: str) -> dict:
    return {"text_run": {"content": text}}


def to_blocks(md: str) -> list[dict]:
    """极简 Markdown → 飞书块。只保标题/正文/列表/引用/代码，表格降级为文本行。"""
    blocks: list[dict] = []
    in_code = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            if line:
                blocks.append({"block_type": 2, "text": {"elements": [_run(line)], "style": {}}})
            continue
        if not line.strip():
            continue
        if line.startswith("#"):
            lvl = len(line) - len(line.lstrip("#"))
            kind = {1: 3, 2: 4, 3: 5, 4: 6}.get(lvl, 6)
            key = f"heading{min(max(lvl, 1), 6)}"
            blocks.append({"block_type": kind, key: {"elements": [_run(line[lvl:].strip())],
                                                     "style": {}}})
        elif line.lstrip().startswith(("- ", "* ")):
            blocks.append({"block_type": 12, "bullet": {
                "elements": [_run(line.lstrip()[2:].strip())], "style": {}}})
        elif line.lstrip().startswith("> "):
            blocks.append({"block_type": 34, "quote": {
                "elements": [_run(line.lstrip()[2:].strip())], "style": {}}})
        else:
            blocks.append({"block_type": 2, "text": {"elements": [_run(line)], "style": {}}})
    return blocks


def append_blocks(doc_id: str, blocks: list[dict], tk: str, env: dict[str, str]) -> None:
    h = {"Authorization": f"Bearer {tk}", "Content-Type": "application/json"}
    for i in range(0, len(blocks), 40):
        chunk = blocks[i:i + 40]
        r = httpx.post(f"{BASE}/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                       headers=h, json={"children": chunk, "index": -1}, timeout=60)
        d = r.json()
        _denied(d, env)
        if d.get("code") != 0:
            raise RuntimeError(f"写入块失败：{d.get('code')} {d.get('msg')}")


def main() -> int:
    md = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/抓取方法-项目详情.md")
    title = md.stem
    env = load_env()
    if not env.get("FEISHU_APP_ID") or not env.get("FEISHU_APP_SECRET"):
        raise SystemExit("缺 FEISHU_APP_ID / FEISHU_APP_SECRET，写在仓库根目录 .env 里")
    tk = token(env)
    print("· token ok")

    try:
        res = import_markdown(md, title, tk, env)
        url = res.get("url") or res.get("ticket_url") or ""
        print(f"· 导入完成：{url}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"· 导入路线不可用（{str(e)[:120]}），改走块追加")

    doc_id, _ = create_doc(title, tk, env)
    print(f"· 建文档 ok：{doc_id}")
    blocks = to_blocks(md.read_text())
    append_blocks(doc_id, blocks, tk, env)
    print(f"· 写入 {len(blocks)} 块")
    print(f"· https://feishu.cn/docx/{doc_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
