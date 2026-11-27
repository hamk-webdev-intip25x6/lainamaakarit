#!/usr/bin/env python3
"""
PR6 Playwright full-page screenshot generator and visual/responsive/theming auditor.

Starts the built-in Django development server on a free local port, logs in as
both regular and admin users, captures full-page (`full_page=True`) screenshots
across desktop and mobile screen sizes and across all 4 contrast/theme modes
(`light`, `dark`, `high-light`, `high-dark`), and verifies:
  1. Zero horizontal overflow (`scrollWidth <= innerWidth`) on every screen size.
  2. Theme selection persists in `localStorage` across page navigations.
  3. Mobile navigation menu opens cleanly on narrow viewports.
  4. Text-to-background luminance contrast meets readability requirements.
"""

from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = BASE_DIR / "docs" / "screenshots"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status < 500:
                    return
        except Exception:
            time.sleep(0.15)
    raise RuntimeError(f"Django-kehityspalvelin ei vastannut ajoissa: {url}")


def audit_page_layout_and_contrast(page, label: str) -> None:
    metrics = page.evaluate(
        """() => {
            const docEl = document.documentElement;
            const bodyStyle = window.getComputedStyle(document.body);
            const card = document.querySelector('.card');
            const cardStyle = card ? window.getComputedStyle(card) : null;

            function parseRgb(str) {
                const m = str.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : [0, 0, 0];
            }
            function relLum([r, g, b]) {
                const conv = [r, g, b].map(v => {
                    const s = v / 255;
                    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
                });
                return 0.2126 * conv[0] + 0.7152 * conv[1] + 0.0722 * conv[2];
            }
            function contrast(c1, c2) {
                const l1 = relLum(parseRgb(c1));
                const l2 = relLum(parseRgb(c2));
                const lighter = Math.max(l1, l2);
                const darker = Math.min(l1, l2);
                return (lighter + 0.05) / (darker + 0.05);
            }

            const bodyContrast = contrast(bodyStyle.color, bodyStyle.backgroundColor);
            const cardContrast = cardStyle
                ? contrast(cardStyle.color, cardStyle.backgroundColor)
                : bodyContrast;

            return {
                scrollWidth: docEl.scrollWidth,
                innerWidth: window.innerWidth,
                dataTheme: docEl.getAttribute('data-theme'),
                storedTheme: window.localStorage.getItem('theme'),
                bodyContrast: Number(bodyContrast.toFixed(2)),
                cardContrast: Number(cardContrast.toFixed(2)),
            };
        }"""
    )

    if metrics["scrollWidth"] > metrics["innerWidth"]:
        raise AssertionError(
            f"[{label}] Vaakasuuntainen ylivuoto havaittu: "
            f"scrollWidth={metrics['scrollWidth']} > innerWidth={metrics['innerWidth']}"
        )

    min_contrast = 12.0 if metrics["dataTheme"] in ("high-light", "high-dark") else 7.0
    if metrics["bodyContrast"] < min_contrast or metrics["cardContrast"] < min_contrast:
        raise AssertionError(
            f"[{label}] Heikko kontrasti tilassa {metrics['dataTheme']}: "
            f"body={metrics['bodyContrast']}:1, card={metrics['cardContrast']}:1"
        )

    print(
        f"  [OK] {label}: width={metrics['scrollWidth']}/{metrics['innerWidth']}px, "
        f"theme={metrics['dataTheme']}, contrast={metrics['cardContrast']}:1"
    )


def set_theme_and_verify(page, base_url: str, theme: str) -> None:
    page.select_option("#theme-select", theme)
    # Reload to confirm localStorage persistence works across page loads
    page.reload(wait_until="networkidle")
    active = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
    stored = page.evaluate("() => window.localStorage.getItem('theme')")
    if active != theme or stored != theme:
        raise AssertionError(f"Teeman tallennus epäonnistui: active={active}, stored={stored}, expected={theme}")


def capture_all() -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    # Ensure DB is migrated and populated before taking screenshots
    subprocess.run([str(BASE_DIR / ".venv/bin/python"), "manage.py", "migrate"], cwd=BASE_DIR, check=True)
    subprocess.run([str(BASE_DIR / ".venv/bin/python"), "manage.py", "populate_db"], cwd=BASE_DIR, check=True)

    server = subprocess.Popen(
        [str(BASE_DIR / ".venv/bin/python"), "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        wait_for_server(f"{base_url}/")
        print(f"Django-palvelin käynnissä osoitteessa {base_url}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            # 1. Desktop anonymous & theme showcase (1280x900)
            desktop_anon = browser.new_context(viewport={"width": 1280, "height": 900})
            page = desktop_anon.new_page()

            page.goto(f"{base_url}/", wait_until="networkidle")
            set_theme_and_verify(page, base_url, "light")
            audit_page_layout_and_contrast(page, "etusivu_vaalea_tyopoyta")
            page.screenshot(path=str(SCREENSHOT_DIR / "etusivu_vaalea_tyopoyta.png"), full_page=True)

            page.goto(f"{base_url}/login/", wait_until="networkidle")
            audit_page_layout_and_contrast(page, "kirjautuminen_vaalea_tyopoyta")
            page.screenshot(path=str(SCREENSHOT_DIR / "kirjautuminen_vaalea_tyopoyta.png"), full_page=True)

            page.goto(f"{base_url}/signup/", wait_until="networkidle")
            audit_page_layout_and_contrast(page, "rekisteroityminen_vaalea_tyopoyta")
            page.screenshot(path=str(SCREENSHOT_DIR / "rekisteroityminen_vaalea_tyopoyta.png"), full_page=True)

            # Catalog in all 4 contrast modes on desktop
            page.goto(f"{base_url}/kirjat/", wait_until="networkidle")
            for mode, fi_slug in [
                ("light", "kirjasto_vaalea_tyopoyta"),
                ("dark", "kirjasto_tumma_tyopoyta"),
                ("high-light", "kirjasto_korkea_vaalea_tyopoyta"),
                ("high-dark", "kirjasto_korkea_tumma_tyopoyta"),
            ]:
                set_theme_and_verify(page, base_url, mode)
                audit_page_layout_and_contrast(page, fi_slug)
                page.screenshot(path=str(SCREENSHOT_DIR / f"{fi_slug}.png"), full_page=True)

            desktop_anon.close()

            # 2. Logged-in reader desktop views (Mikko Virtanen has an active loan on The Lean Startup)
            desktop_user = browser.new_context(viewport={"width": 1280, "height": 900})
            upage = desktop_user.new_page()
            upage.goto(f"{base_url}/login/", wait_until="networkidle")
            set_theme_and_verify(upage, base_url, "light")
            upage.fill('input[name="username"]', "mikko_virtanen")
            upage.fill('input[name="password"]', "MikkoLukija2026!")
            upage.click('button[type="submit"]')
            upage.wait_for_load_state("networkidle")

            audit_page_layout_and_contrast(upage, "etusivu_kirjautunut_tyopoyta")
            upage.screenshot(path=str(SCREENSHOT_DIR / "etusivu_kirjautunut_tyopoyta.png"), full_page=True)

            # Navigate to The Lean Startup book detail & reviews page
            upage.goto(f"{base_url}/kirjat/?q=Lean+Startup", wait_until="networkidle")
            upage.click(".book-title a")
            upage.wait_for_load_state("networkidle")
            audit_page_layout_and_contrast(upage, "kirja_ja_arvostelut_vaalea_tyopoyta")
            upage.screenshot(path=str(SCREENSHOT_DIR / "kirja_ja_arvostelut_vaalea_tyopoyta.png"), full_page=True)

            set_theme_and_verify(upage, base_url, "dark")
            audit_page_layout_and_contrast(upage, "kirja_ja_arvostelut_tumma_tyopoyta")
            upage.screenshot(path=str(SCREENSHOT_DIR / "kirja_ja_arvostelut_tumma_tyopoyta.png"), full_page=True)

            set_theme_and_verify(upage, base_url, "light")
            upage.goto(f"{base_url}/omat-lainat/", wait_until="networkidle")
            audit_page_layout_and_contrast(upage, "omat_lainat_vaalea_tyopoyta")
            upage.screenshot(path=str(SCREENSHOT_DIR / "omat_lainat_vaalea_tyopoyta.png"), full_page=True)
            desktop_user.close()

            # 3. Admin Huolto view (Aino Korhonen, is_staff=True)
            desktop_admin = browser.new_context(viewport={"width": 1280, "height": 900})
            apage = desktop_admin.new_page()
            apage.goto(f"{base_url}/login/", wait_until="networkidle")
            apage.fill('input[name="username"]', "aino_korhonen")
            apage.fill('input[name="password"]', "AinoKirjasto2026!")
            apage.click('button[type="submit"]')
            apage.wait_for_load_state("networkidle")

            apage.goto(f"{base_url}/huolto/", wait_until="networkidle")
            set_theme_and_verify(apage, base_url, "light")
            audit_page_layout_and_contrast(apage, "huolto_vaalea_tyopoyta")
            apage.screenshot(path=str(SCREENSHOT_DIR / "huolto_vaalea_tyopoyta.png"), full_page=True)

            set_theme_and_verify(apage, base_url, "high-dark")
            audit_page_layout_and_contrast(apage, "huolto_korkea_tumma_tyopoyta")
            apage.screenshot(path=str(SCREENSHOT_DIR / "huolto_korkea_tumma_tyopoyta.png"), full_page=True)
            desktop_admin.close()

            # 4. Mobile responsiveness & theming audit (375x812 phone screen)
            mobile_ctx = browser.new_context(viewport={"width": 375, "height": 812}, is_mobile=True)
            mpage = mobile_ctx.new_page()
            mpage.goto(f"{base_url}/login/", wait_until="networkidle")
            mpage.fill('input[name="username"]', "mikko_virtanen")
            mpage.fill('input[name="password"]', "MikkoLukija2026!")
            mpage.click('button[type="submit"]')
            mpage.wait_for_load_state("networkidle")

            # Open mobile navigation menu and verify no horizontal overflow
            mpage.goto(f"{base_url}/kirjat/", wait_until="networkidle")
            mpage.click("#nav-toggle")
            audit_page_layout_and_contrast(mpage, "kirjasto_mobiili_valikko_auki")
            mpage.screenshot(path=str(SCREENSHOT_DIR / "kirjasto_mobiili_vaalea.png"), full_page=True)

            # High-contrast dark on mobile book detail & reviews page
            mpage.select_option("#theme-select", "high-dark")
            mpage.goto(f"{base_url}/kirjat/?q=Paasikiven", wait_until="networkidle")
            mpage.click(".book-title a")
            mpage.wait_for_load_state("networkidle")
            audit_page_layout_and_contrast(mpage, "kirja_mobiili_korkea_tumma")
            mpage.screenshot(path=str(SCREENSHOT_DIR / "kirja_mobiili_korkea_tumma.png"), full_page=True)

            mobile_ctx.close()
            browser.close()

        print(f"Kaikki kuvakaappaukset tallennettu kansioon: {SCREENSHOT_DIR}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    capture_all()
