"""企业概况原版式填充：正文须插入 DOM，评价组表保留合并表头。"""

from io import BytesIO

import pytest
from docx import Document

from apps.reports.overview_fill import fill_overview_bytes


@pytest.fixture
def sample_overview_data():
    return {
        "s1_1_1": "任务来源正文",
        "s1_1_2": "研究目标",
        "s1_1_4": "成果",
        "s1_1_5": "产品",
        "s1_2_1": "进展",
        "s1_2_2": "指标",
        "s1_2_3": "部件",
        "s1_2_4": "软件",
        "s2_1": "目的",
        "s2_2": "依据",
        "s2_3": "人员说明",
        "s2_4": "评价过程说明",
        "self_rows": [{"role": "组长", "name": "甲", "title": "高工", "specialty": "光学", "org": "甲公司"}],
        "third_rows": [{"role": "组长", "name": "乙", "title": "教授", "specialty": "评价", "org": "乙所"}],
        "ch3_sections": [
            ("3.1 X 射线源", "光管已完成型式试验。"),
            ("3.2 探测器", "探测器性能达标。"),
        ],
    }


def test_overview_fill_inserts_ch3_body_and_team_table(sample_overview_data):
    doc = Document(BytesIO(fill_overview_bytes(sample_overview_data)))
    paragraphs = list(doc.paragraphs)
    h31 = next(p for p in paragraphs if p.text.strip() == "3.1 X 射线源")
    h31_index = paragraphs.index(h31)
    assert paragraphs[h31_index + 1].text.strip() == "光管已完成型式试验。"
    h32 = next(p for p in paragraphs if p.text.strip() == "3.2 探测器")
    assert paragraphs[paragraphs.index(h32) + 1].text.strip() == "探测器性能达标。"
    assert any(p.text.strip() == "评价过程说明" for p in paragraphs)
    team = doc.tables[1]
    assert team.rows[0].cells[0].text.strip() == "自评估工作组"
    assert team.rows[1].cells[0].text.strip() == "角色"
    assert team.rows[2].cells[1].text.strip() == "甲"
    third_row = next(i for i, r in enumerate(team.rows) if r.cells[0].text.strip() == "第三方评价组")
    assert team.rows[third_row + 2].cells[1].text.strip() == "乙"