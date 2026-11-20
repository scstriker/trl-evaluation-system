"""大功率波长色散 X 荧光光谱仪：本地对比用企业材料（原版式排版）。"""

from io import BytesIO

from django.conf import settings
from docx import Document
from pathlib import Path

from apps.reports.overview_fill import ensure_enterprise_overview_template, fill_overview_bytes

BASE = Path(settings.BASE_DIR)


def _comparison_data():
    return {
        "s1_1_1": (
            "针对生物医药、新材料、矿物冶金、环境监测等领域对元素成分与膜厚快速分析的需求，"
            "我国在高端大功率波长色散 X 射线荧光光谱仪（WDXRF）领域长期依赖进口。"
            "本项目来源于工业和信息化部装备工业一司，由钢研纳克检测技术股份有限公司牵头，"
            "联合探测器、高压电源、测角与软件等单位研制 CNX-838 顺序式 WDXRF。"
        ),
        "s1_1_2": (
            "研制大功率端窗激发与分光测角核心部件，建成 CNX-838 整机；"
            "在半导体辅材、冶金炉料等场景完成方法验证，指标达到任务书要求；"
            "形成设计、工艺、测试与软件成套文件。"
        ),
        "s1_1_3": "下列指标与任务书对照，样机阶段实测结果见附件说明（本对比稿仅列文字）。",
        "indicator_table": (
            ["序号", "部件", "指南技术指标", "项目达成情况"],
            [
                ["1", "X 射线源", "大功率端窗光管", "4 kW 样管完成型式试验"],
                ["2", "探测器", "能量分辨率满足轻元素分析", "达到设计指标"],
                ["3", "光学测角", "角度重复性≤0.0002°", "通过计量校准"],
                ["4", "软件", "定量化与厚度分析", "完成 TRL5 验证"],
            ],
        ),
        "s1_1_4": "样机 1 套；分系统与软件；企业标准、测试大纲与报告、说明书；专利与论文若干。",
        "s1_1_5": (
            "CNX-838 适用于多元素定性定量与镀层厚度测量，配置大功率激发、单晶分光与真空进样，"
            "软件支持多标样校准与数据归档。"
        ),
        "s1_2_1": "完成 TRL1～4 对应论证、部件验证与工程样机集成，具备本次评价条件。",
        "s1_2_2": "主要技术指标达标；轻元素通道晶体配套持续优化。",
        "s1_2_3": "光管、探测器、晶体、测角等部件完成研制与检测。",
        "s1_2_4": "CNX-838 软件完成需求、设计与原型验证及安全测评。",
        "s2_1": "确认 CNX-838 是否达到技术就绪度 4 级；范围 TRL1～TRL4 全部适用条目。",
        "s2_2": "GB/T 22900-2022；《技术就绪度评价标准及细则》。",
        "s2_3": "成立自评估工作组与第三方评价组，组成见表 2-1。",
        "s2_4": (
            "企业自评并上传佐证；评价机构组织第三方组审核；"
            "通过后企业提交概况、WBST 与支撑信息，系统合并生成本报告。"
        ),
        "self_rows": [
            {"role": "组长", "name": "李工", "title": "高级工程师", "specialty": "X 射线光学", "org": "核芯光电科技（山东）有限公司"},
            {"role": "成员", "name": "陈工", "title": "工程师", "specialty": "探测器", "org": "核芯光电科技（山东）有限公司"},
        ],
        "third_rows": [
            {"role": "组长", "name": "杜工", "title": "正高级工程师", "specialty": "测量控制设备评价", "org": "机械工业仪器仪表综合技术经济研究所"},
        ],
        "ch3_sections": [
            ("3.1 X 射线源", "完成大功率端窗光管结构设计、热管理及型式试验。"),
            ("3.2 探测器", "闪烁体探测器及前放完成装配与性能测试。"),
            ("3.3 分光晶体", "多层膜晶体镀膜与分光效率达到设计预期。"),
            ("3.4 测角仪", "精密测角与闭环控制通过干涉比对验证。"),
            ("3.5 软件", "采集、定量、厚度与控制模块完成样机联调。"),
            ("3.6 大功率波长色散 X 荧光光谱仪整机", "整机组装与实验室性能测试完成。"),
        ],
    }


def build_overview_docx() -> bytes:
    return fill_overview_bytes(_comparison_data())


def build_support_docx() -> bytes:
    tpl = BASE / "data" / "templates" / "enterprise" / "评价支撑信息-模板.docx"
    if tpl.is_file():
        doc = Document(str(tpl))
    else:
        doc = Document()
        doc.add_paragraph("附录B  评价支撑信息清单")
    if doc.tables:
        table = doc.tables[0]
        items = [
            ("1", "X 射线光管型式试验报告", "TRL4-HW-1"),
            ("2", "探测器性能测试报告", "TRL4-HW-2"),
            ("3", "整机性能测试报告", "TRL4-HW-5"),
            ("4", "软件需求规格说明", "TRL4-SW-1"),
        ]
        for row in items:
            cells = table.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = v
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def warm_enterprise_template():
    """部署时生成/刷新企业概况模板文件。"""
    ensure_enterprise_overview_template()