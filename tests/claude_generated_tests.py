"""
EcoAlgo — Economic Algorithms Platform
Comprehensive Test Suite (Playwright + pytest)

Covers:
  • Public pages & navigation
  • SEO / meta / responsive viewport
  • Auth flows (register, login, logout, validation)
  • Protected-page access control
  • Algorithm module pages (post-login)
  • UI components, links, footer
  • Performance & security headers
  • Accessibility basics

Setup:
    pip install pytest playwright
    playwright install chromium
Run:
    pytest ecoalgo_tests.py -v --headed        # watch in browser
    pytest ecoalgo_tests.py -v                  # headless
    pytest ecoalgo_tests.py -v -k "not auth"   # skip auth tests
"""

import re
import time
import uuid
import pytest
from playwright.sync_api import sync_playwright, expect, Page, Browser

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
BASE_URL = "https://ecoalgo.csariel.xyz"

# Set these to a REAL test account if you want login-dependent tests to pass.
# If left as-is, the auth tests will attempt registration with a throwaway email.
TEST_EMAIL = f"test_{uuid.uuid4().hex[:8]}@mailinator.com"
TEST_PASSWORD = "Str0ng!Pass#2026"

# Reuse a known account for smoke-testing protected pages (optional).
EXISTING_EMAIL = None   # e.g. "demo@example.com"
EXISTING_PASSWORD = None

ROUTES = {
    "home":     "/",
    "login":    "/login",
    "register": "/register",
}

# Algorithm module paths that likely exist behind auth
ALGORITHM_PATHS = [
    "/fair-allocation",
    "/fair-allocation-capacities",
    "/approval-voting",
    "/participatory-budgeting",
]


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────
@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        yield br
        br.close()


@pytest.fixture()
def page(browser: Browser):
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (EcoAlgo-TestBot/1.0; "
            "+https://ecoalgo.csariel.xyz) Playwright"
        ),
    )
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture()
def mobile_page(browser: Browser):
    ctx = browser.new_context(
        viewport={"width": 375, "height": 812},
        is_mobile=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile",
    )
    pg = ctx.new_page()
    yield pg
    ctx.close()


# ──────────────────────────────────────────────
# 1. HOMEPAGE TESTS
# ──────────────────────────────────────────────
class TestHomepage:
    """Validate the public landing page."""

    def test_page_loads_and_title(self, page: Page):
        resp = page.goto(BASE_URL)
        assert resp and resp.ok, f"Homepage returned {resp.status if resp else 'None'}"
        assert "EcoAlgo" in page.title()

    def test_hero_heading_present(self, page: Page):
        page.goto(BASE_URL)
        heading = page.locator("h1")
        expect(heading).to_be_visible()
        expect(heading).to_contain_text("Economic Algorithms")

    def test_hero_subtext(self, page: Page):
        page.goto(BASE_URL)
        body_text = page.text_content("body") or ""
        assert "fair division" in body_text.lower()
        assert "voting" in body_text.lower()
        assert "participatory budgeting" in body_text.lower()

    def test_four_algorithm_cards_displayed(self, page: Page):
        page.goto(BASE_URL)
        cards = page.locator("h5, .card-title, [class*='card'] h5")
        expect(cards).to_have_count(4)

    def test_algorithm_card_titles(self, page: Page):
        page.goto(BASE_URL)
        text = page.text_content("body") or ""
        expected = [
            "Fair Allocation",
            "Approval Voting",
            "Participatory Budgeting",
        ]
        for title in expected:
            assert title in text, f"Missing algorithm card: {title}"

    def test_cta_buttons_visible(self, page: Page):
        page.goto(BASE_URL)
        login_btn = page.locator("a[href*='login']").first
        register_btn = page.locator("a[href*='register']").first
        expect(login_btn).to_be_visible()
        expect(register_btn).to_be_visible()

    def test_footer_present(self, page: Page):
        page.goto(BASE_URL)
        footer = page.locator("footer")
        expect(footer).to_be_visible()
        expect(footer).to_contain_text("EcoAlgo")


# ──────────────────────────────────────────────
# 2. NAVIGATION TESTS
# ──────────────────────────────────────────────
class TestNavigation:
    """Navbar links, brand logo, and cross-page routing."""

    def test_navbar_brand_links_home(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.locator("a.navbar-brand, nav a:has-text('EcoAlgo')").first.click()
        page.wait_for_url(f"{BASE_URL}/")

    def test_nav_login_link(self, page: Page):
        page.goto(BASE_URL)
        page.locator("a[href*='login']").first.click()
        page.wait_for_url(f"**login**")
        assert "Login" in (page.title() or page.text_content("body") or "")

    def test_nav_register_link(self, page: Page):
        page.goto(BASE_URL)
        page.locator("a[href*='register']").first.click()
        page.wait_for_url(f"**register**")
        assert "Register" in (page.title() or page.text_content("body") or "")

    def test_login_page_has_register_link(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        link = page.locator("a:has-text('Register')")
        expect(link.first).to_be_visible()

    def test_register_page_has_login_link(self, page: Page):
        page.goto(f"{BASE_URL}/register")
        link = page.locator("a:has-text('Login')")
        expect(link.first).to_be_visible()

    def test_no_broken_nav_links(self, page: Page):
        page.goto(BASE_URL)
        links = page.locator("nav a[href]").all()
        for link in links:
            href = link.get_attribute("href") or ""
            if href.startswith("http") or href.startswith("/"):
                url = href if href.startswith("http") else f"{BASE_URL}{href}"
                resp = page.request.get(url)
                assert resp.ok, f"Broken nav link: {url} → {resp.status}"


# ──────────────────────────────────────────────
# 3. REGISTRATION TESTS
# ──────────────────────────────────────────────
class TestRegistration:
    """Registration form validation and submission."""

    def test_register_page_loads(self, page: Page):
        resp = page.goto(f"{BASE_URL}/register")
        assert resp and resp.ok

    def test_register_form_elements(self, page: Page):
        page.goto(f"{BASE_URL}/register")
        expect(page.locator("input[type='email'], input[name*='email']").first).to_be_visible()
        expect(page.locator("input[type='password'], input[name*='password']").first).to_be_visible()
        expect(page.locator("button[type='submit'], input[type='submit']").first).to_be_visible()

    def test_register_empty_submission(self, page: Page):
        """Submitting blank form should not navigate away / should show error."""
        page.goto(f"{BASE_URL}/register")
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_timeout(1000)
        # Should still be on the register page (validation failed)
        assert "register" in page.url.lower()

    def test_register_invalid_email(self, page: Page):
        page.goto(f"{BASE_URL}/register")
        page.locator("input[type='email'], input[name*='email']").first.fill("not-an-email")
        page.locator("input[type='password'], input[name*='password']").first.fill(TEST_PASSWORD)
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_timeout(1000)
        assert "register" in page.url.lower()

    def test_register_short_password(self, page: Page):
        page.goto(f"{BASE_URL}/register")
        page.locator("input[type='email'], input[name*='email']").first.fill(TEST_EMAIL)
        page.locator("input[type='password'], input[name*='password']").first.fill("ab")
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_timeout(1000)
        # Expect still on register or an error message
        assert "register" in page.url.lower() or page.locator(".alert, .error, .invalid-feedback").count() > 0

    def test_register_valid_submission(self, page: Page):
        """
        Attempts to register a new account.
        Behaviour depends on server state — test verifies no crash.
        """
        page.goto(f"{BASE_URL}/register")
        email_field = page.locator("input[type='email'], input[name*='email']").first
        pass_field = page.locator("input[type='password'], input[name*='password']").first
        email_field.fill(TEST_EMAIL)
        pass_field.fill(TEST_PASSWORD)
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_timeout(2000)
        # Should either redirect (success) or show an error (duplicate, etc.)
        body = page.text_content("body") or ""
        navigated_away = "register" not in page.url.lower()
        has_feedback = bool(re.search(r"(already|exists|error|success|welcome)", body, re.I))
        assert navigated_away or has_feedback, "Registration produced no observable result"


# ──────────────────────────────────────────────
# 4. LOGIN TESTS
# ──────────────────────────────────────────────
class TestLogin:
    """Login form validation, error handling, and happy-path."""

    def test_login_page_loads(self, page: Page):
        resp = page.goto(f"{BASE_URL}/login")
        assert resp and resp.ok

    def test_login_form_elements(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        expect(page.locator("input[type='email'], input[name*='email']").first).to_be_visible()
        expect(page.locator("input[type='password'], input[name*='password']").first).to_be_visible()
        expect(page.locator("button[type='submit'], input[type='submit']").first).to_be_visible()

    def test_remember_me_checkbox(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        checkbox = page.locator("input[type='checkbox']").first
        expect(checkbox).to_be_visible()

    def test_login_empty_submission(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_timeout(1000)
        assert "login" in page.url.lower()

    def test_login_wrong_credentials(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.locator("input[type='email'], input[name*='email']").first.fill("nobody@invalid.test")
        page.locator("input[type='password'], input[name*='password']").first.fill("WrongPass!1")
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_timeout(2000)
        # Should still be on login or show error
        body = page.text_content("body") or ""
        still_on_login = "login" in page.url.lower()
        has_error = bool(re.search(r"(invalid|incorrect|error|fail|wrong)", body, re.I))
        assert still_on_login or has_error

    @pytest.mark.skipif(
        EXISTING_EMAIL is None,
        reason="Set EXISTING_EMAIL / EXISTING_PASSWORD to run",
    )
    def test_login_valid_credentials(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        page.locator("input[type='email'], input[name*='email']").first.fill(EXISTING_EMAIL)
        page.locator("input[type='password'], input[name*='password']").first.fill(EXISTING_PASSWORD)
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_timeout(3000)
        assert "login" not in page.url.lower(), "Did not redirect after valid login"


# ──────────────────────────────────────────────
# 5. ACCESS CONTROL TESTS
# ──────────────────────────────────────────────
class TestAccessControl:
    """Protected pages should redirect unauthenticated users."""

    @pytest.mark.parametrize("path", ALGORITHM_PATHS)
    def test_unauthenticated_redirect(self, page: Page, path: str):
        resp = page.goto(f"{BASE_URL}{path}", wait_until="networkidle")
        # Should either 403, redirect to login, or return 404 (unknown route)
        redirected_to_login = "login" in page.url.lower()
        not_found = resp and resp.status == 404
        forbidden = resp and resp.status == 403
        assert redirected_to_login or not_found or forbidden, (
            f"Protected route {path} was accessible without auth (status {resp.status if resp else '?'})"
        )


# ──────────────────────────────────────────────
# 6. SEO & META TESTS
# ──────────────────────────────────────────────
class TestSeoMeta:
    """Basic SEO hygiene checks."""

    def test_meta_viewport(self, page: Page):
        page.goto(BASE_URL)
        viewport = page.locator("meta[name='viewport']")
        content = viewport.get_attribute("content") or ""
        assert "width=device-width" in content

    def test_title_tag_not_empty(self, page: Page):
        page.goto(BASE_URL)
        assert len(page.title()) > 0

    def test_no_duplicate_h1(self, page: Page):
        page.goto(BASE_URL)
        h1_count = page.locator("h1").count()
        assert h1_count == 1, f"Expected 1 <h1>, found {h1_count}"

    @pytest.mark.parametrize("route", ["/", "/login", "/register"])
    def test_lang_attribute(self, page: Page, route: str):
        page.goto(f"{BASE_URL}{route}")
        html_tag = page.locator("html")
        lang = html_tag.get_attribute("lang")
        # Should have a lang attribute for accessibility
        assert lang and len(lang) >= 2, f"Missing lang attribute on {route}"


# ──────────────────────────────────────────────
# 7. RESPONSIVE / MOBILE TESTS
# ──────────────────────────────────────────────
class TestResponsive:
    """Mobile viewport rendering."""

    def test_homepage_mobile_renders(self, mobile_page: Page):
        resp = mobile_page.goto(BASE_URL)
        assert resp and resp.ok
        expect(mobile_page.locator("h1")).to_be_visible()

    def test_login_mobile_renders(self, mobile_page: Page):
        mobile_page.goto(f"{BASE_URL}/login")
        expect(mobile_page.locator("input[type='email'], input[name*='email']").first).to_be_visible()

    def test_no_horizontal_overflow(self, mobile_page: Page):
        mobile_page.goto(BASE_URL)
        overflow = mobile_page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        assert not overflow, "Page has horizontal overflow on mobile viewport"


# ──────────────────────────────────────────────
# 8. PERFORMANCE TESTS
# ──────────────────────────────────────────────
class TestPerformance:
    """Basic load-time and resource checks."""

    def test_homepage_loads_under_5s(self, page: Page):
        start = time.monotonic()
        page.goto(BASE_URL, wait_until="networkidle")
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"Homepage took {elapsed:.1f}s to load"

    def test_login_page_loads_under_5s(self, page: Page):
        start = time.monotonic()
        page.goto(f"{BASE_URL}/login", wait_until="networkidle")
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"Login page took {elapsed:.1f}s to load"

    def test_no_console_errors(self, page: Page):
        errors: list[str] = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(BASE_URL, wait_until="networkidle")
        # Filter out benign third-party errors
        real_errors = [e for e in errors if "favicon" not in e.lower()]
        assert len(real_errors) == 0, f"Console errors: {real_errors}"

    def test_no_failed_network_requests(self, page: Page):
        failures: list[str] = []
        page.on(
            "requestfailed",
            lambda req: failures.append(f"{req.url} → {req.failure}"),
        )
        page.goto(BASE_URL, wait_until="networkidle")
        assert len(failures) == 0, f"Failed requests: {failures}"


# ──────────────────────────────────────────────
# 9. SECURITY HEADER TESTS
# ──────────────────────────────────────────────
class TestSecurityHeaders:
    """Check recommended security headers."""

    @pytest.fixture(autouse=True)
    def _load_headers(self, page: Page):
        self.response = page.goto(BASE_URL)

    def test_https_enforced(self, page: Page):
        assert page.url.startswith("https://"), "Site not served over HTTPS"

    def test_x_content_type_options(self, page: Page):
        val = self.response.header_value("x-content-type-options")
        assert val == "nosniff", f"X-Content-Type-Options: {val}"

    def test_x_frame_options(self, page: Page):
        val = self.response.header_value("x-frame-options")
        # DENY or SAMEORIGIN both acceptable
        assert val and val.upper() in ("DENY", "SAMEORIGIN"), f"X-Frame-Options: {val}"

    def test_content_type_present(self, page: Page):
        ct = self.response.header_value("content-type")
        assert ct and "text/html" in ct


# ──────────────────────────────────────────────
# 10. ACCESSIBILITY BASICS
# ──────────────────────────────────────────────
class TestAccessibility:
    """Lightweight a11y checks (not a full audit)."""

    def test_images_have_alt(self, page: Page):
        page.goto(BASE_URL)
        images = page.locator("img").all()
        for img in images:
            alt = img.get_attribute("alt")
            assert alt is not None, f"Image missing alt: {img.get_attribute('src')}"

    def test_form_inputs_have_labels(self, page: Page):
        page.goto(f"{BASE_URL}/login")
        inputs = page.locator("input:not([type='hidden']):not([type='checkbox'])").all()
        for inp in inputs:
            inp_id = inp.get_attribute("id") or ""
            inp_name = inp.get_attribute("name") or ""
            placeholder = inp.get_attribute("placeholder") or ""
            aria_label = inp.get_attribute("aria-label") or ""
            has_label = page.locator(f"label[for='{inp_id}']").count() > 0 if inp_id else False
            assert has_label or placeholder or aria_label, (
                f"Input '{inp_name}' has no label, placeholder, or aria-label"
            )

    def test_focus_visible_on_login(self, page: Page):
        """Tab through login form — focused element should be visible."""
        page.goto(f"{BASE_URL}/login")
        page.keyboard.press("Tab")
        focused = page.evaluate("document.activeElement?.tagName")
        assert focused and focused.lower() in ("input", "a", "button", "select", "textarea")

    @pytest.mark.parametrize("route", ["/", "/login", "/register"])
    def test_color_contrast_text_exists(self, page: Page, route: str):
        """Sanity: body text is not invisible (color != background)."""
        page.goto(f"{BASE_URL}{route}")
        result = page.evaluate("""
            () => {
                const el = document.querySelector('h1, p, label');
                if (!el) return {color: 'none', bg: 'none'};
                const s = getComputedStyle(el);
                return {color: s.color, bg: s.backgroundColor};
            }
        """)
        assert result["color"] != result["bg"], "Text may be invisible (same fg/bg)"


# ──────────────────────────────────────────────
# 11. LINK INTEGRITY
# ──────────────────────────────────────────────
class TestLinkIntegrity:
    """All on-page links should resolve without 5xx errors."""

    @pytest.mark.parametrize("route", ["/", "/login", "/register"])
    def test_all_links_resolve(self, page: Page, route: str):
        page.goto(f"{BASE_URL}{route}")
        anchors = page.locator("a[href]").all()
        for a in anchors:
            href = a.get_attribute("href") or ""
            if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
                continue
            url = href if href.startswith("http") else f"{BASE_URL}{href}"
            resp = page.request.get(url)
            assert resp.status < 500, f"Server error on {url}: {resp.status}"


# ──────────────────────────────────────────────
# 12. CSRF / FORM SECURITY
# ──────────────────────────────────────────────
class TestFormSecurity:
    """Forms should include CSRF tokens if server-rendered."""

    @pytest.mark.parametrize("route", ["/login", "/register"])
    def test_csrf_token_present(self, page: Page, route: str):
        page.goto(f"{BASE_URL}{route}")
        csrf = page.locator("input[name='csrfmiddlewaretoken'], input[name='_csrf'], input[name='_token'], meta[name='csrf-token']")
        # If the framework uses CSRF, there should be a hidden field or meta tag
        # This is informational — skip if the framework doesn't use classic CSRF tokens
        if csrf.count() == 0:
            pytest.skip("No classic CSRF token found (may use cookie-based CSRF or SPA auth)")

    @pytest.mark.parametrize("route", ["/login", "/register"])
    def test_password_field_is_masked(self, page: Page, route: str):
        page.goto(f"{BASE_URL}{route}")
        pw = page.locator("input[type='password']").first
        assert pw.get_attribute("type") == "password"


# ──────────────────────────────────────────────
# 13. ERROR HANDLING
# ──────────────────────────────────────────────
class TestErrorHandling:
    """Server behaviour on bad routes."""

    def test_404_on_unknown_route(self, page: Page):
        resp = page.goto(f"{BASE_URL}/this-page-does-not-exist-xyz")
        assert resp and resp.status in (404, 302, 301), (
            f"Unknown route returned {resp.status} instead of 404 or redirect"
        )

    def test_404_page_not_blank(self, page: Page):
        page.goto(f"{BASE_URL}/this-page-does-not-exist-xyz")
        body = page.text_content("body") or ""
        assert len(body.strip()) > 0, "404 page is completely blank"
