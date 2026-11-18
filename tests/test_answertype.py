"""按回答类型选条款：单元测试。"""
from app.retrieval import answertype as at


def test_detect_prefers_number():
    assert at.detect("2019年补充计发基本养老金的标准是多少？") == "num"
    assert at.detect("失业金能领几个月") == "num"


def test_detect_date_list_procedure():
    assert at.detect("这个政策什么时候开始执行") == "date"
    assert at.detect("办理需要哪些材料") == "list"
    assert at.detect("怎么申请工伤认定") == "procedure"


def test_detect_default():
    assert at.detect("上海市人力资源和社会保障局") == "default"
    assert at.detect("") == "default"


def test_bonus_number_clause():
    text = "每人计发320元；按照本人缴费年限，每满1年计发3元。"
    assert at.bonus(text, "num") > 0
    assert at.bonus("本市社会保险经办机构应简化手续", "num") == 0.0


def test_bonus_is_capped():
    text = "；".join(f"第{i}项{i}元" for i in range(20))
    assert at.bonus(text, "num") <= at.MAX_BONUS


def test_bonus_default_zero():
    assert at.bonus("每人计发320元", "default") == 0.0


def test_dates_are_not_numeric_facts():
    """"2019年4月1日"不是"天数"事实——否则日期条款会压过写金额的那条。"""
    assert at.bonus("本通知自2019年4月1日起实施。", "num") == 0.0
    assert at.bonus("每人每月计发305元。", "num") > 0


def test_related_clauses_are_attached_and_citable():
    from app.retrieval.search import search_result
    ans = search_result("2019年补充计发基本养老金的标准是多少", k=5)
    assert ans.hits and ans.hits[0].related, "第一名应带同文件补充条款"
    r = ans.hits[0].related[0]
    assert r["text"] and r["path"], "补充条款要能给出路径，引用才可核验"
