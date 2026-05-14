"""
Load tests for the Fair Division app.

Run with:
    locust -f tests/locustfile.py --host=http://localhost:5032

Open http://localhost:8089 to use the web UI, or add --headless -u <users> -r <rate> -t <time>.

User classes (pick via --class-picker in the web UI or --users on the CLI):
  - LoginCycleUser          : simple  – measures login/logout performance
  - BrowseUser              : simple  – anonymous home page browsing
  - AuthBrowseUser          : medium  – logged-in user browses survey pages
  - SurveyFlowUser          : complex – full moderator+participant lifecycle per user
  - AlgorithmLoadUser       : complex – algorithm execution under concurrent load
                                        WARNING: CPU-intensive, keep user count low (2-5).
  - ConcurrentParticipantUser: complex – many participants ranking the same survey simultaneously
                                        Most realistic production scenario.
                                        Each virtual user self-registers and joins a shared survey
                                        created at test start and deleted at test stop.
"""

import re
import uuid
import random
import requests as _requests
from locust import HttpUser, task, between, events


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


def _get_csrf(client, url):
    """GET a page and return its CSRF token. Used before any state-changing POST."""
    r = client.get(url, name=f"{url} [CSRF fetch]")
    return _extract_csrf(r.text)


def _extract_invite_code(html):
    """Parse invite code from the survey edit page."""
    match = re.search(r"/join/([^\"]+)\"", html)
    return match.group(1) if match else None


def _register_and_login(client):
    """
    Register a fresh unique user and return (email, password).
    Flask-Security logs the user in automatically on registration
    and grants the moderator role via the on_user_registered hook.
    Use this instead of logging in as admin so tests don't depend
    on the admin password being correct in every environment.
    """
    email = f"loadtest_{uuid.uuid4().hex[:10]}@test.com"
    password = "Testpass1!"

    r = client.get("/register")
    csrf = _extract_csrf(r.text)
    client.post(
        "/register",
        data={"email": email, "password": password, "csrf_token": csrf},
        allow_redirects=True,
    )
    return email, password


def _login(client, email, password):
    """Login and mark the request as failed if the login page is returned."""
    r = client.get("/login")
    csrf = _extract_csrf(r.text)
    r = client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": csrf, "remember": "false"},
        allow_redirects=True,
    )
    if "/login" in r.url:
        r.failure(f"Login failed for {email} — still on login page after POST")


# ---------------------------------------------------------------------------
# Shared survey state for ConcurrentParticipantUser
# Set by @events.test_start, read by each virtual user in on_start.
# ---------------------------------------------------------------------------

_shared_survey_id = None
_shared_invite_code = None


@events.test_start.add_listener
def create_shared_survey(environment, **kwargs):
    """
    Runs once before any virtual user spawns.
    Admin creates one survey with items and opens it.
    All ConcurrentParticipantUser instances will join this survey.
    """
    global _shared_survey_id, _shared_invite_code

    host = environment.host
    session = _requests.Session()

    # Login as admin
    r = session.get(f"{host}/login")
    csrf = _extract_csrf(r.text)
    session.post(f"{host}/login", data={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "csrf_token": csrf,
        "remember": "false",
    })

    # Create the shared survey
    r = session.get(f"{host}/surveys/create")
    csrf = _extract_csrf(r.text)
    r = session.post(f"{host}/surveys/create", data={
        "title": "Concurrent Participant Load Test",
        "description": "Shared survey created by locust",
        "category": "fair_division",
        "ranking_mode": "ordinal",
        "csrf_token": csrf,
    }, allow_redirects=False)
    survey_id = _extract_survey_id(r)
    if not survey_id:
        print("[locust] ERROR: could not create shared survey — ConcurrentParticipantUser will be skipped")
        return
    _shared_survey_id = survey_id

    # Add items (JSON endpoint, no CSRF needed)
    for name in ITEMS:
        session.post(f"{host}/surveys/{survey_id}/items/add", data={"name": name},
                     headers={"X-Requested-With": "XMLHttpRequest"})

    # Open survey
    r = session.get(f"{host}/surveys/{survey_id}")
    csrf = _extract_csrf(r.text)
    session.post(f"{host}/surveys/{survey_id}/toggle", data={"csrf_token": csrf}, allow_redirects=False)

    # Get the invite code from the edit page
    r = session.get(f"{host}/surveys/{survey_id}")
    _shared_invite_code = _extract_invite_code(r.text)

    session.get(f"{host}/logout")
    print(f"[locust] Shared survey created: id={survey_id}, invite={_shared_invite_code}")


@events.test_stop.add_listener
def delete_shared_survey(environment, **kwargs):
    """Runs once after the test ends. Admin deletes the shared survey."""
    if not _shared_survey_id:
        return

    host = environment.host
    session = _requests.Session()

    r = session.get(f"{host}/login")
    csrf = _extract_csrf(r.text)
    session.post(f"{host}/login", data={
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "csrf_token": csrf,
        "remember": "false",
    })
    r = session.get(f"{host}/surveys/{_shared_survey_id}")
    csrf = _extract_csrf(r.text)
    session.post(f"{host}/surveys/{_shared_survey_id}/delete", data={"csrf_token": csrf}, allow_redirects=False)
    session.get(f"{host}/logout")
    print(f"[locust] Shared survey {_shared_survey_id} deleted")


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
        _login(self.client, ADMIN_EMAIL, ADMIN_PASSWORD)
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
        _login(self.client, ADMIN_EMAIL, ADMIN_PASSWORD)

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
    """
    wait_time = between(1, 3)

    survey_id = None
    item_ids = []

    def on_start(self):
        # --- Register a fresh user (gets moderator role automatically) ---
        _register_and_login(self.client)

        # --- Create survey (fetch page first to get CSRF token) ---
        csrf = _get_csrf(self.client, "/surveys/create")
        response = self.client.post(
            "/surveys/create",
            data={
                "title": f"Load Test Survey {random.randint(1000, 9999)}",
                "description": "Created by locust",
                "category": "fair_division",
                "ranking_mode": "ordinal",
                "csrf_token": csrf,
            },
            allow_redirects=False,
        )
        self.survey_id = _extract_survey_id(response)
        if not self.survey_id:
            return

        # --- Add items (JSON endpoint, no CSRF needed) ---
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
        csrf = _get_csrf(self.client, f"/surveys/{self.survey_id}")
        self.client.post(
            f"/surveys/{self.survey_id}/toggle",
            data={"csrf_token": csrf},
            allow_redirects=False,
        )

        # --- Join as participant via invite link ---
        edit_page = self.client.get(f"/surveys/{self.survey_id}")
        invite_code = _extract_invite_code(edit_page.text)
        if invite_code:
            self.client.get(f"/join/{invite_code}", allow_redirects=True)

    def on_stop(self):
        if self.survey_id:
            csrf = _get_csrf(self.client, f"/surveys/{self.survey_id}")
            self.client.post(
                f"/surveys/{self.survey_id}/delete",
                data={"csrf_token": csrf},
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


# ---------------------------------------------------------------------------
# Level 3 continued – Algorithm execution
# ---------------------------------------------------------------------------

class AlgorithmLoadUser(HttpUser):
    """
    Stress-tests algorithm execution specifically.

    on_start : log in, create a survey, add items, open it, join and submit
               rankings — same setup as SurveyFlowUser, because the algorithm
               route requires at least one participant with rankings to exist.
    task     : POST run-algorithm with round_robin. Each execution is CPU-bound
               and runs in the app's thread pool with a 60s timeout.
    on_stop  : delete the survey, log out.

    Recommended concurrency: 2–5 users.
    Each user owns its own survey so runs don't share state.

    Run headless example (5 users, 30s):
        locust -f tests/locustfile.py --host=http://localhost:5032 \\
               --headless -u 5 -r 1 -t 30s --class-picker AlgorithmLoadUser
    """
    wait_time = between(5, 10)  # algorithm runs take seconds; don't hammer immediately

    survey_id = None
    item_ids = []

    def on_start(self):
        # --- Register a fresh user (gets moderator role automatically) ---
        _register_and_login(self.client)

        # --- Create survey ---
        csrf = _get_csrf(self.client, "/surveys/create")
        response = self.client.post(
            "/surveys/create",
            data={
                "title": f"Algo Load Test {random.randint(1000, 9999)}",
                "description": "Created by locust AlgorithmLoadUser",
                "category": "fair_division",
                "ranking_mode": "ordinal",
                "csrf_token": csrf,
            },
            allow_redirects=False,
        )
        self.survey_id = _extract_survey_id(response)
        if not self.survey_id:
            return

        # --- Add items (JSON endpoint, no CSRF needed) ---
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
        csrf = _get_csrf(self.client, f"/surveys/{self.survey_id}")
        self.client.post(
            f"/surveys/{self.survey_id}/toggle",
            data={"csrf_token": csrf},
            allow_redirects=False,
        )

        # --- Join as participant ---
        edit_page = self.client.get(f"/surveys/{self.survey_id}")
        invite_code = _extract_invite_code(edit_page.text)
        if invite_code:
            self.client.get(f"/join/{invite_code}", allow_redirects=True)

        # --- Submit initial rankings so the algorithm has data to work with ---
        if self.item_ids:
            shuffled = self.item_ids[:]
            random.shuffle(shuffled)
            data = {f"rank_{item_id}": rank + 1 for rank, item_id in enumerate(shuffled)}
            self.client.post(f"/surveys/{self.survey_id}/rank", data=data)

    def on_stop(self):
        if self.survey_id:
            csrf = _get_csrf(self.client, f"/surveys/{self.survey_id}")
            self.client.post(
                f"/surveys/{self.survey_id}/delete",
                data={"csrf_token": csrf},
                allow_redirects=False,
            )
        self.client.get("/logout")

    @task
    def run_algorithm(self):
        if not self.survey_id:
            return
        self.client.post(
            f"/surveys/{self.survey_id}/run-algorithm",
            data={"algorithm": "round_robin"},
            name="/surveys/[id]/run-algorithm",
        )


# ---------------------------------------------------------------------------
# Level 3 continued – Concurrent participants on the same survey
# ---------------------------------------------------------------------------

class ConcurrentParticipantUser(HttpUser):
    """
    Many participants submitting rankings to the same survey simultaneously.

    This is the most realistic production scenario: a moderator has shared an
    invite link and many users are ranking at the same time. It stresses:
      - concurrent writes to ItemRanking (DELETE existing + INSERT new per user)
      - concurrent SurveyParticipant lookups
      - database row locking under write contention

    Setup (handled by @events.test_start / test_stop above):
      A single shared survey is created before users spawn and deleted after the
      test ends. The survey ID and invite code are stored in module-level vars.

    Each virtual user:
      on_start : self-registers with a unique email, joins the shared survey,
                 reads item IDs from the rank page.
      task     : submits a random ordinal ranking.
      on_stop  : logs out.

    Note: registered test accounts are left in the database after the test.
    Clean them up manually or with a DB query:
      DELETE FROM user WHERE email LIKE 'loadtest_%@test.com';

    Run headless example (20 users, ramp 5/s, run 60s):
        locust -f tests/locustfile.py --host=http://localhost:5032 \\
               --headless -u 20 -r 5 -t 60s --class-picker ConcurrentParticipantUser
    """
    wait_time = between(1, 3)

    item_ids = []

    def on_start(self):
        if not _shared_survey_id or not _shared_invite_code:
            return

        # Register a fresh unique account for this virtual user
        self.email = f"loadtest_{uuid.uuid4().hex[:10]}@test.com"
        self.password = "Testpass1!"

        r = self.client.get("/register")
        csrf = _extract_csrf(r.text)
        self.client.post(
            "/register",
            data={
                "email": self.email,
                "password": self.password,
                "csrf_token": csrf,
            },
            allow_redirects=True,
        )

        # Join the shared survey via invite link
        self.client.get(f"/join/{_shared_invite_code}", allow_redirects=True)

        # Read item IDs from the rank page so we can build valid form data
        r = self.client.get(
            f"/surveys/{_shared_survey_id}/rank",
            name="/surveys/[id]/rank [GET]",
        )
        self.item_ids = [int(i) for i in re.findall(r'name="rank_(\d+)"', r.text)]

    def on_stop(self):
        self.client.get("/logout")

    @task
    def submit_rankings(self):
        if not _shared_survey_id or not self.item_ids:
            return
        shuffled = self.item_ids[:]
        random.shuffle(shuffled)
        data = {f"rank_{item_id}": rank + 1 for rank, item_id in enumerate(shuffled)}
        self.client.post(
            f"/surveys/{_shared_survey_id}/rank",
            data=data,
            name="/surveys/[id]/rank [POST]",
        )
