"""Снимки экрана для руководств (docs/guides) со стенда.

Наборы «login» и «admin» только смотрят: входят под учётными записями и открывают страницы,
ничего не создают и не меняют. Набор «lesson» проводит одно учебное занятие: преподаватель
создаёт занятие «Руководство — удалить» («Задания формирует ИИ», сложность 1, звонок заявителю
разрешён), обучаемый принимает карточку, звонит начальнику аварийной бригады и завершает
карточку, после съёмки занятие завершается и удаляется. На снимках название занятия заменено на
«Учебное занятие 3». Снимки ложатся в docs/images/guides/.

    python scripts/guide_screenshots.py --base https://155-212-186-2.sslip.io --password 112112112
    python scripts/guide_screenshots.py ... --only admin

Нужен пакет playwright для Python и браузер Chromium (python -m playwright install chromium).
"""

import argparse
import re
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "images" / "guides"
VIEWPORT = {"width": 1440, "height": 900}
# Pause after the network calms down: the pages poll and animate their tiles.
SETTLE_MS = 1500
LESSON_TITLE = "Руководство — удалить"
SHOWN_TITLE = "Учебное занятие 3"


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
# The test lesson is named so that nobody keeps it; the guide shows an ordinary title instead.
RENAME = """([from, to]) => {
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode())
    if (n.nodeValue.includes(from)) n.nodeValue = n.nodeValue.split(from).join(to);
  for (const el of document.querySelectorAll("input"))
    if (el.value.includes(from)) el.value = el.value.split(from).join(to);
}"""


def shot(page: Page, name: str, *, mask=None, clip=True, height=MAX_HEIGHT, settle=SETTLE_MS) -> None:
    """``clip``: the content column (True), the whole viewport (False) or the page from the top
    down to ``height`` («top»)."""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(settle)
    page.evaluate(RENAME, [LESSON_TITLE, SHOWN_TITLE])
    area = None
    if clip == "top":
        area = {"x": 0, "y": 0, "width": VIEWPORT["width"], "height": height}
    elif clip:
        area = page.evaluate(CONTENT_CLIP)
        if area:
            area["height"] = min(area["height"], height)
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


def remove_lessons(t: Page, base: str) -> None:
    """Finish and delete every test lesson, also the one a failed run left behind."""
    t.goto(f"{base}/teacher")
    t.wait_for_load_state("networkidle")
    for href in [a.get_attribute("href") for a in t.get_by_role("link", name=LESSON_TITLE).all()]:
        t.goto(f"{base}{href}")
        t.wait_for_load_state("networkidle")
        if t.get_by_role("button", name="Завершить занятие").count():
            t.get_by_role("button", name="Завершить занятие").click()
            t.get_by_role("button", name="Да, завершить").click()
            t.wait_for_timeout(3000)
    t.goto(f"{base}/teacher")
    t.wait_for_load_state("networkidle")
    rows = t.locator("tr", has=t.get_by_role("link", name=LESSON_TITLE))
    if rows.count():
        for i in range(rows.count()):
            rows.nth(i).locator("input[type=checkbox]").check()
        t.get_by_role("button", name="Удалить выбранные").click()
        t.get_by_role("button", name="Да, удалить").click()
        t.wait_for_timeout(2000)
    t.goto(f"{base}/teacher")
    t.wait_for_load_state("networkidle")
    left = t.get_by_role("link", name=LESSON_TITLE).count()
    print("   тестовых занятий осталось:", left)
    if left:
        raise SystemExit("Тестовое занятие не удалилось: удалите его на стенде вручную.")


def call_brigade(s: Page) -> None:
    """Dial the brigade chief from the directory, name the card and send the brigade out."""
    body = s.locator("body").inner_text()
    address = s.get_by_text(re.compile(r"Москва,")).first.inner_text()
    street = re.sub(r",?\s*\d+\S*$", "", address.split("Москва,")[-1]).strip()
    house = re.search(r"(\d+)\S*$", address).group(1)
    kind = re.search(r"Класс\.:\s*([^;\n]+)", body)
    hurt = "Есть пострадавший." if re.search(r"Пострадавшие:\s*да", body) else "Пострадавших нет."
    s.locator("button", has_text="Начальник аварийной бригады").first.click()
    s.get_by_role("button", name="Вызов").click()
    box = s.locator("input[placeholder*='скажите'], input[placeholder*='напишите']").first
    box.wait_for(timeout=30_000)
    transcript = s.locator("[aria-label='Стенограмма звонка']")
    s.wait_for_timeout(3000)
    for phrase, answer in (
        (f"{street}, дом {house}. {kind.group(1).strip() if kind else ''}. {hurt}", r"\S"),
        ("Выезжайте.", r"Наряд|выезжаем|машин"),
    ):
        before = transcript.inner_text()
        box.fill(phrase)
        box.press("Enter")
        for _ in range(60):
            s.wait_for_timeout(1000)
            now = transcript.inner_text()
            if "отвечает" not in now.splitlines()[-1] and re.search(answer, now[len(before):]):
                break
    print("   разговор:", " | ".join(transcript.inner_text().splitlines()[-4:]))


def lesson(t: Page, base: str, password: str) -> None:
    s = t.context.browser.new_context(viewport=VIEWPORT, device_scale_factor=1.5, locale="ru-RU").new_page()
    login(t, base, "teacher", password)
    remove_lessons(t, base)
    try:
        open_and_shot(t, base, "/teacher/groups", "teacher-groups")
        open_and_shot(t, base, "/teacher/phones", "teacher-phones")
        t.goto(f"{base}/teacher/sessions/new")
        t.wait_for_load_state("networkidle")
        t.fill("#title", LESSON_TITLE)
        t.locator("[data-testid=ai-cards]").check()
        # With AI cards the lesson needs the DDS service; the incident groups appear after it.
        t.get_by_label("Городское хозяйство (ЕДЦ ЖКХ)").check()
        t.get_by_label(re.compile("Разрешить звонок заявителю")).check()
        shot(t, "teacher-session-form", clip="top", height=1850, settle=800)
        t.get_by_role("button", name="Создать занятие").click()
        t.wait_for_url(re.compile(r"/teacher/sessions/[0-9a-f-]+$"))
        session_url = t.url
        t.get_by_role("button", name="Начать занятие").click()
        t.wait_for_timeout(2000)

        login(s, base, "student", password)
        s.wait_for_url(re.compile("/journal"))
        row = s.locator("tbody tr").first
        row.wait_for(timeout=120_000)
        shot(s, "journal", clip="top", height=280)
        row.click()
        s.wait_for_url(re.compile("/student/attempts/"))
        shot(s, "card", clip=False, settle=2000)
        t.goto(session_url)
        shot(t, "teacher-monitor", settle=2500)

        s.get_by_role("button", name="Проставить статус").first.click()
        s.get_by_label("Статус", exact=True).select_option(label="Принята")
        s.get_by_label("Сохранить статус").click()
        s.wait_for_timeout(2000)
        s.get_by_role("button", name="Телефон", exact=True).click()
        shot(s, "phone-dial", clip=False, settle=1000)
        call_brigade(s)
        shot(s, "phone-call", clip=False)
        s.get_by_role("button", name="Завершить звонок").click()
        s.wait_for_timeout(1500)
        s.get_by_role("button", name="Свернуть телефон").click()

        finish = s.get_by_role("button", name="Завершить работу с карточкой")
        if not finish.is_visible():
            s.get_by_role("button", name="Развернуть панель тренажёра").click()
        finish.click()
        s.get_by_role("button", name="Да, завершить").click()
        review = s.get_by_role("link", name="Открыть разбор")
        review.wait_for(timeout=90_000)
        shot(s, "card-finished", clip=False)
        s.goto(base + review.get_attribute("href"))
        shot(s, "review", clip="top", height=1010, settle=3000)
        open_and_shot(s, base, "/student/progress", "progress", height=1400)
        open_and_shot(s, base, "/student", "student-assignments")

        open_and_shot(t, base, session_url.removeprefix(base) + "/report", "teacher-report")
        open_and_shot(t, base, "/teacher", "teacher-sessions")
        open_and_shot(t, base, "/teacher/scenarios", "teacher-scenarios")
        open_and_shot(t, base, "/teacher/analytics", "teacher-analytics", settle=2500)
    finally:
        s.context.close()
        remove_lessons(t, base)


SETS = {"login": login_page, "admin": admin, "lesson": lesson}


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
