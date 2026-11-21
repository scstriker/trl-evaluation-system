"""Batch UI audit screenshots at 1280x800. Requires local runserver + seed_sample.

Usage:
  python scripts/ui_audit_shot.py
  python scripts/ui_audit_shot.py --role gangyan
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "ui-audit" / "screenshots"
PASSWORD = "Trl@2026"

P_DUOLIU = "5dd79777-7e25-4699-9fa5-1a19bc1493f6"
A_DUOLIU = "373a5386-d047-4200-81ad-cdaeb5539f64"
R_DUOLIU = "841300fd-bc3a-4f28-a51b-653b6a24ee3d"
T_E01 = "58"
I_L5 = "f816490f-4238-4f83-8bd5-117799027343"
P_ZHONGNAN = "454daaa3-6e43-4bd2-861d-f36d6e2e911c"
E_01 = "1"

SHOTS = {
    "anon": [
        ("login", "/login/"),
        ("register", "/register/"),
        ("err-404", "/this-page-does-not-exist-ui-audit/"),
    ],
    "gangyan": [
        ("account", "/account/"),
        ("trl-list", "/trl/"),
        ("mrl-list", "/mrl/"),
        ("report-list", "/reports/"),
        ("trl-detail", f"/projects/{P_DUOLIU}/trl/"),
        ("trl-detail-l5", f"/projects/{P_DUOLIU}/trl/?level=5"),
        ("trl-item-modal", f"/projects/{P_DUOLIU}/trl/?level=5&item={I_L5}"),
        ("mrl-detail", f"/projects/{P_DUOLIU}/mrl/"),
        ("report-preview", f"/reports/projects/{P_DUOLIU}/"),
        ("project-new", "/projects/new/"),
        ("review-history", f"/reviews/assessments/{A_DUOLIU}/history/"),
        ("inbox", "/inbox/"),
        ("rules-process", "/rules/process/"),
        ("err-403", "/users/"),
    ],
    "admin": [
        ("account", "/account/"),
        ("trl-list", "/trl/"),
        ("mrl-list", "/mrl/"),
        ("report-list", "/reports/"),
        ("review-board", "/reviews/"),
        ("expert-list", "/experts/"),
        ("user-list", "/users/"),
        ("audit-list", "/audit/"),
        ("recycle-bin", "/recycle-bin/"),
        ("trl-detail", f"/projects/{P_DUOLIU}/trl/"),
        ("report-preview", f"/reports/projects/{P_DUOLIU}/"),
        ("front-matter", f"/reports/projects/{P_ZHONGNAN}/front-matter/"),
        ("round-detail", f"/reviews/rounds/{R_DUOLIU}/"),
        ("round-assign", f"/reviews/rounds/{R_DUOLIU}/assign/"),
        ("review-history", f"/reviews/assessments/{A_DUOLIU}/history/"),
        ("expert-detail", f"/experts/{E_01}/"),
        ("expert-new", "/experts/new/"),
        ("expert-fees", "/experts/fees/"),
        ("rules-process", "/rules/process/"),
        ("rules-trl", "/rules/trl/"),
        ("rules-mrl", "/rules/mrl/"),
        ("settings", "/settings/"),
        ("inbox", "/inbox/"),
        ("project-new", "/projects/new/"),
    ],
    "expert01": [
        ("account", "/account/"),
        ("task-list", "/reviews/tasks/"),
        ("task-detail", f"/reviews/tasks/{T_E01}/"),
        ("inbox", "/inbox/"),
    ],
}


def make_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1280,800")
    opts.add_argument("--force-device-scale-factor=1")
    opts.add_argument("--hide-scrollbars")
    driver = webdriver.Chrome(options=opts)
    driver.set_window_size(1280, 800)
    return driver


def login(driver: webdriver.Chrome, username: str) -> None:
    driver.get(f"{BASE}/login/")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "id_username")))
    user = driver.find_element(By.ID, "id_username")
    pwd = driver.find_element(By.ID, "id_password")
    user.clear()
    user.send_keys(username)
    pwd.clear()
    pwd.send_keys(PASSWORD)
    driver.find_element(By.CSS_SELECTOR, "form button[type=submit]").click()
    WebDriverWait(driver, 10).until(lambda d: "/login/" not in d.current_url)


def logout(driver: webdriver.Chrome) -> None:
    # base.html keeps logout form in a modal; submit it then wipe cookies.
    submitted = False
    try:
        submitted = bool(
            driver.execute_script(
                """
                const form = document.querySelector("form[action*='logout']");
                if (!form) return false;
                form.submit();
                return true;
                """
            )
        )
    except Exception:
        submitted = False
    if submitted:
        time.sleep(0.6)
    driver.delete_all_cookies()
    driver.get(f"{BASE}/login/")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "id_username")))


def shot(driver: webdriver.Chrome, role: str, slug: str, path: str) -> None:
    dest_dir = OUT / role
    dest_dir.mkdir(parents=True, exist_ok=True)
    driver.get(f"{BASE}{path}")
    time.sleep(0.45)
    # reopen auto-modal if data-auto-modal present
    driver.execute_script(
        """
        const id = document.body.getAttribute('data-auto-modal');
        if (id) {
          const el = document.getElementById(id);
          if (el) { el.classList.add('open'); el.style.display = 'flex'; }
        }
        """
    )
    time.sleep(0.2)
    dest = dest_dir / f"{slug}.png"
    driver.save_screenshot(str(dest))
    print(f"OK  {role}/{slug}.png  <- {path}  [{driver.title[:40]}]")


def run(roles: list[str]) -> None:
    driver = make_driver()
    try:
        for role in roles:
            print(f"=== {role} ===")
            if role == "anon":
                for slug, path in SHOTS["anon"]:
                    shot(driver, "anon", slug, path)
                continue
            login(driver, role)
            for slug, path in SHOTS[role]:
                try:
                    shot(driver, role, slug, path)
                except Exception as exc:  # noqa: BLE001
                    print(f"FAIL {role}/{slug}: {exc}")
            logout(driver)
    finally:
        driver.quit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=sorted(SHOTS), action="append")
    args = parser.parse_args()
    roles = args.role or list(SHOTS)
    run(roles)


if __name__ == "__main__":
    main()
