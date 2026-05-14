"""
Load tests for the Fair Division app.

Run with:
    locust -f tests/locustfile.py --host=http://localhost:5032

Open http://localhost:8089 to use the web UI, or add --headless -u <users> -r <rate> -t <time>.

User classes (pick via --class-picker in the web UI or --users on the CLI):
  - LoginCycleUser   : simple – measures login/logout performance
  - BrowseUser       : simple – anonymous home page browsing
  - AuthBrowseUser   : medium – logged-in user browses survey pages
  - SurveyFlowUser   : complex – full moderator+participant lifecycle per user
"""

import re
import random
from locust import HttpUser, task, between


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "adminpassword"

ITEMS = ["Item A", "Item B", "Item C", "Item D", "Item E"]


def _extract_csrf(html):
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    if not match:
        match = re.search(r'value="([^"]+)"[^>]*name="csrf_token"', html)
    return match.group(1) if match else ""


def _extract_survey_id(response):
    """Parse survey ID from the redirect URL after survey creation."""
    location = response.headers.get("Location", "")
    match = re.search(r"/surveys/(\d+)", location)
    return int(match.group(1)) if match else None


def _extract_invite_code(html):
    """Parse invite code from the survey edit page."""
    match = re.search(r"/join/([^\"]+)\"", html)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Level 1 – Simple
# ---------------------------------------------------------------------------

class LoginCycleUser(HttpUser):
    """
    Measures the cost of the full login → logout round trip.
    Each task iteration is one complete login cycle.
    """
    wait_time = between(1, 3)

    @task
    def login_then_logout(self):
        response = self.client.get("/login", name="/login [GET]")
        csrf = _extract_csrf(response.text)

        self.client.post(
            "/login",
            name="/login [POST]",
            data={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "csrf_token": csrf,
                "remember": "false",
            },
            allow_redirects=True,
        )

        self.client.get("/logout")


class BrowseUser(HttpUser):
    """
    Anonymous user hitting the public home page.
    No auth needed — tests raw page-render throughput.
    """
    wait_time = between(1, 5)

    @task
    def home(self):
        self.client.get("/")


# ---------------------------------------------------------------------------
# Level 2 – Medium
# ---------------------------------------------------------------------------

class AuthBrowseUser(HttpUser):
    """
    Logged-in user browsing their survey pages.
    Logs in once at startup; tasks simulate a moderator checking their dashboard.
    """
    wait_time = between(2, 5)

    def on_start(self):
        response = self.client.get("/login")
        csrf = _extract_csrf(response.text)
        self.client.post(
            "/login",
            data={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "csrf_token": csrf,
                "remember": "false",
            },
            allow_redirects=True,
        )

    def on_stop(self):
        self.client.get("/logout")

    @task(3)
    def survey_list(self):
        self.client.get("/surveys")

    @task(2)
    def my_surveys(self):
        self.client.get("/my-surveys")

    @task(1)
    def home(self):
        self.client.get("/")


# ---------------------------------------------------------------------------
# Level 3 – Complex
# ---------------------------------------------------------------------------

class SurveyFlowUser(HttpUser):
    """
    Full moderator + participant lifecycle per virtual user.

    on_start : log in, create a survey, add items, open it, join as participant.
    task      : submit rankings, view results.
    on_stop   : delete the survey, log out.

    Each Locust user owns its own survey to avoid write conflicts between users.
    No CSRF tokens are needed for these routes (WTF_CSRF_CHECK_DEFAULT = False).
    """
    wait_time = between(1, 3)

    survey_id = None
    item_ids = []

    def on_start(self):
        # --- Login ---
        response = self.client.get("/login")
        csrf = _extract_csrf(response.text)
        self.client.post(
            "/login",
            data={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD,
                "csrf_token": csrf,
                "remember": "false",
            },
            allow_redirects=True,
        )

        # --- Create survey ---
        response = self.client.post(
            "/surveys/create",
            data={
                "title": f"Load Test Survey {random.randint(1000, 9999)}",
                "description": "Created by locust",
                "category": "fair_item_allocation",
                "ranking_mode": "ordinal",
            },
            allow_redirects=False,
        )
        self.survey_id = _extract_survey_id(response)
        if not self.survey_id:
            return

        # --- Add items ---
        self.item_ids = []
        for name in ITEMS:
            r = self.client.post(
                f"/surveys/{self.survey_id}/items/add",
                data={"name": name},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            data = r.json()
            if data.get("success"):
                self.item_ids.append(data["item_id"])

        # --- Open survey ---
        self.client.post(
            f"/surveys/{self.survey_id}/toggle",
            allow_redirects=False,
        )

        # --- Join as participant via invite link ---
        edit_page = self.client.get(f"/surveys/{self.survey_id}")
        invite_code = _extract_invite_code(edit_page.text)
        if invite_code:
            self.client.get(f"/join/{invite_code}", allow_redirects=True)

    def on_stop(self):
        if self.survey_id:
            self.client.post(
                f"/surveys/{self.survey_id}/delete",
                allow_redirects=False,
            )
        self.client.get("/logout")

    @task(3)
    def submit_rankings(self):
        if not self.survey_id or not self.item_ids:
            return
        # Ordinal ranking: assign a unique rank to each item
        shuffled = self.item_ids[:]
        random.shuffle(shuffled)
        data = {f"rank_{item_id}": rank + 1 for rank, item_id in enumerate(shuffled)}
        self.client.post(
            f"/surveys/{self.survey_id}/rank",
            data=data,
            name="/surveys/[id]/rank [POST]",
        )

    @task(1)
    def view_results(self):
        if not self.survey_id:
            return
        self.client.get(
            f"/surveys/{self.survey_id}/results",
            name="/surveys/[id]/results",
        )

    @task(1)
    def view_rank_page(self):
        if not self.survey_id:
            return
        self.client.get(
            f"/surveys/{self.survey_id}/rank",
            name="/surveys/[id]/rank [GET]",
        )
