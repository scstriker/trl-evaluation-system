# -*- coding: utf-8 -*-
"""开发期一次性脚本：从上级目录 app_GUI.py 的常量生成规范化种子 CSV。

用法：在 trl-evaluation-system/ 目录下执行 `python scripts/build_seed.py`
"""

import csv
import importlib.util
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_GUI = BASE_DIR.parent / "app_GUI.py"
SEED_DIR = BASE_DIR / "data" / "seed"


def load_app_gui():
    spec = importlib.util.spec_from_file_location("app_gui_source", APP_GUI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    module = load_app_gui()
    SEED_DIR.mkdir(parents=True, exist_ok=True)

    rules_path = SEED_DIR / "trl_rules_seed.csv"
    with open(rules_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["row_type", "level", "track", "seq", "name", "evidence"])
        for level in range(1, 10):
            writer.writerow(["level", level, "", "", module.LEVEL_NAMES[level], ""])
            for track, criteria in (("hw", module.HW_CRITERIA), ("sw", module.SW_CRITERIA)):
                for item in criteria.get(level, []):
                    writer.writerow(["item", level, track, item["seq"], item["name"], item["evidence"]])

    mrl_path = SEED_DIR / "mrl_framework_seed.csv"
    with open(mrl_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["level", "name", "desc"])
        for entry in module.MRL_FRAMEWORK:
            level = int(entry["level"].replace("MRL", "").strip())
            writer.writerow([level, entry["name"], entry["desc"]])

    hw = sum(len(v) for v in module.HW_CRITERIA.values())
    sw = sum(len(v) for v in module.SW_CRITERIA.values())
    print(f"已生成 {rules_path.name}（硬件 {hw} 条 + 软件 {sw} 条 = {hw + sw} 条）与 {mrl_path.name}（{len(module.MRL_FRAMEWORK)} 级）")


if __name__ == "__main__":
    main()
