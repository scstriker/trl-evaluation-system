"""评价体系配置：技术就绪度（TRL）与制造成熟度（MRL）共用同一套评价流程。"""

from dataclasses import dataclass

TRL = "trl"
MRL = "mrl"

SYSTEM_CHOICES = [
    (TRL, "技术就绪度"),
    (MRL, "制造成熟度"),
]


@dataclass(frozen=True)
class EvaluationSystem:
    code: str
    abbr: str
    name: str
    max_level: int
    standard: str
    basis: tuple

    @property
    def levels(self):
        return range(1, self.max_level + 1)

    @property
    def report_title(self):
        return f"{self.name}评价报告"

    def level_label(self, level):
        return f"{self.abbr} {level}"


SYSTEMS = {
    TRL: EvaluationSystem(
        code=TRL,
        abbr="TRL",
        name="技术就绪度",
        max_level=9,
        standard="《技术就绪度评价标准及细则》",
        basis=(
            ("GB/T 22900-2022", "《科学技术研究项目评价通则》"),
            ("GB/T 41621-2022", "《科学技术研究项目评价实施指南 开发研究项目》"),
            ("—", "《技术就绪度评价标准及细则》"),
        ),
    ),
    MRL: EvaluationSystem(
        code=MRL,
        abbr="MRL",
        name="制造成熟度",
        max_level=10,
        standard="《制造成熟度评价标准及细则》",
        basis=(
            ("GB/T 22900-2022", "《科学技术研究项目评价通则》"),
            ("—", "《制造成熟度评价标准及细则》"),
        ),
    ),
}


def get_system(code):
    return SYSTEMS[code]
