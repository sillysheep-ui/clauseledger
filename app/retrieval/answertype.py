"""按"回答类型"给条款加分。

问题在于**选条款**，不是召回：检索到对的文件、挑错那一条。
数字型问题最典型——问"标准是多少"，答案那条只写"320 元"，
没有"标准"两个字，BM25 靠词重合打分，它自然排不上。

所以这里做两件事：
1. `detect()` 判断问题要什么（数量/日期/名单/流程）；
2. `bonus()` 给"含该类内容"的条款加分，**只在已经排进前几名的文件内部**生效
   （见 `search.py::_answer_type_boost`）——挑错文件比挑错条款更贵，
   我们不在这一层改变"是哪个文件"，只改"给这个文件的哪一条"。

规则是手写的，不是学出来的：这个领域里"要数字就要有数字+单位"是硬约束，
不需要模型也能判。代价是会漏掉中文数字（"六十五周岁"），
这一点记在 docs/ANSWER-EVAL.md 的已知局限里。
"""
from __future__ import annotations

import re

# 数字 + 单位：与 tools/eval_answer.py 的事实抽取同源，保证"能加分"和"能核验"是同一把尺子
# 先剥掉完整日期，否则"2019年4月1日"里的"1日"会被当成"天数"事实（与 tools/eval_answer 同口径）
FULL_DATE = re.compile(r"(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?|\d{1,2}\s*月\s*\d{1,2}\s*日")
NUM_UNIT = re.compile(
    r"\d+(?:\.\d+)?\s*(?:万元|元|%|％|个月|个工作日|工作日|月|日|天|小时|岁|分|倍|人|次)"
)
DATE = re.compile(r"(?:19|20)\d{2}\s*年|\d{1,2}\s*月\s*\d{1,2}\s*日|自.{0,12}起(?:施行|实施|执行|生效)")
LIST = re.compile(r"、|（[一二三四五六七八九十]+）|^\s*\d+[.．、]")

NUM_Q = ("多少", "几", "标准", "金额", "比例", "百分比", "元", "基数", "上限", "下限",
         "最高", "最低", "不超过", "不得低于", "天数", "岁", "倍", "分数")
DATE_Q = ("什么时候", "何时", "多久", "期限", "有效期", "截止", "哪年", "几年", "多长时间")
LIST_Q = ("哪些", "什么材料", "需要什么", "条件", "名单", "目录", "清单", "范围")
PROC_Q = ("怎么", "如何", "流程", "办理", "申请", "手续", "步骤", "去哪儿", "去哪里")

MAX_BONUS = 3.0


def detect(question: str) -> str:
    """返回 `num` / `date` / `list` / `procedure` / `default`。

    顺序按"越具体越优先"：问"多少钱、多久"时先看数量，
    因为期限类问题常常也能用数字回答（"30 日"）。
    """
    q = question or ""
    if any(w in q for w in NUM_Q):
        return "num"
    if any(w in q for w in DATE_Q):
        return "date"
    if any(w in q for w in LIST_Q):
        return "list"
    if any(w in q for w in PROC_Q):
        return "procedure"
    return "default"


def bonus(text: str, atype: str) -> float:
    """这条条款"像不像"该类型问题的答案。加分上限 `MAX_BONUS`。"""
    if not text or atype == "default":
        return 0.0
    if atype == "num":
        n = len(NUM_UNIT.findall(FULL_DATE.sub(" ", text)))
        # 0.9/个：条款里出现 1 个数字就值得往前挪一点，
        # 但不能让一条满是数字的表格压过真正的答案句——所以封顶 3.0
        return min(MAX_BONUS, 0.9 * n)
    if atype == "date":
        return 1.2 if DATE.search(text) else 0.0
    if atype == "list":
        n = len(LIST.findall(text))
        return min(1.5, 0.5 * n)
    if atype == "procedure":
        n = sum(text.count(w) for w in ("办理", "申请", "提交", "材料", "流程", "手续"))
        return min(1.2, 0.4 * n)
    return 0.0
