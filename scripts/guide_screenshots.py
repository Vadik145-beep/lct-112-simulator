"""Снимки экрана для руководств (docs/guides) со стенда.

Скрипт только смотрит: входит под учётными записями и открывает страницы, ничего не создаёт и
не меняет. Снимки ложатся в docs/images/guides/.

    python scripts/guide_screenshots.py --base https://155-212-186-2.sslip.io --password 112112112
    python scripts/guide_screenshots.py ... --only admin

Нужен пакет playwright для Python и браузер Chromium (python -m playwright install chromium).
"""

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "images" / "guides"
VIEWPORT = {"width": 1440, "height": 900}
# Pause after the network calms down: the pages poll and animate their tiles.
SETTLE_MS = 1500


def login(page: Page, base: str, user: str, password: str) -> None:
    page.goto(f"{base}/login")
    page.fill("#login", user)
    page.fill("#password", password)
    page.locator("button[type=submit]").click()
    page.wait_for_url(lambda url: "/login" not in url)
    page.wait_for_load_state("networkidle")


# The cabinets keep their content in a centred column; the guide shows that column with the menu
# and no more than this height, so the text stays readable on an A4 page.
MAX_HEIGHT = 1000
CONTENT_CLIP = """() => {
  const main = document.querySelector("main");
  const content = main && main.firstElementChild;
  if (!content) return null;
  const m = main.getBoundingClientRect();
  const c = content.getBoundingClientRect();
  return { x: m.left, y: 0, width: m.width, height: c.bottom + window.scrollY + 24 };
}"""


def shot(page: Page, name: str, *, mask=None, clip=True) -> None:
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(SETTLE_MS)
    area = page.evaluate(CONTENT_CLIP) if clip else None
    if area:
        area["height"] = min(area["height"], MAX_HEIGHT)
    page.screenshot(path=OUT / f"{name}.png", mask=mask or [], mask_color="#d9d9d9",
                    clip=area, full_page=bool(area))
    print("  ", name)


def open_and_shot(page: Page, base: str, path: str, name: str, **kw) -> None:
    page.goto(f"{base}{path}")
    shot(page, name, **kw)


def login_page(page: Page, base: str, _password: str) -> None:
    page.goto(f"{base}/login")
    shot(page, "login")


def admin(page: Page, base: str, password: str) -> None:
    login(page, base, "admin", password)
    open_and_shot(page, base, "/admin", "admin-users")
    # The form only opens; «Отмена» closes it without creating anybody.
    page.get_by_role("button", name="Создать пользователя").first.click()
    form = page.get_by_role("form", name="Новый пользователь")
    form.wait_for()
    shot(page, "admin-user-create")
    form.get_by_role("button", name="Отмена").click()
    open_and_shot(page, base, "/admin/services", "admin-services")
    open_and_shot(page, base, "/admin/health", "admin-health")
    page.goto(f"{base}/admin/audit")
    page.wait_for_load_state("networkidle")
    # The addresses of the people who used the stand are not for the guide.
    shot(page, "admin-audit", mask=[page.locator("table tbody tr td:last-child")])
    open_and_shot(page, base, "/admin/backups", "admin-backups")
    open_and_shot(page, base, "/admin/settings", "admin-settings")


SETS = {"login": login_page, "admin": admin}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="адрес стенда")
    parser.add_argument("--password", required=True, help="пароль учётных записей стенда")
    parser.add_argument("--only", choices=sorted(SETS), action="append", help="какие наборы снять")
    parser.add_argument("--browser", help="путь к Chromium, если нужен не встроенный")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.browser)
        for name in args.only or list(SETS):
            print(name)
            context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1.5, locale="ru-RU")
            SETS[name](context.new_page(), args.base.rstrip("/"), args.password)
            context.close()
        browser.close()


if __name__ == "__main__":
    main()
