"""
Load tests for the Fair Division app.

User mix (500 total):
  - ReturningParticipant (weight 9, ~400 users): pre-seeded user, login → join → rank → view results
  - NewParticipant       (weight 1, ~50 users):  registers fresh during the test (realistic trickle)
  - ModeratorUser        (fixed 5):              pre-seeded moderator, browses admin pages

Run:
    locust -f tests/locustfile.py --host=http://localhost:5032
    # headless 500 users, ramp 5/sec:
    locust -f tests/locustfile.py --host=http://localhost:5032 \
        --headless -u 500 -r 5 -t 5m --only-summary
"""

import json
import random
import threading
from pathlib import Path

from bs4 import BeautifulSoup
from locust import HttpUser, task, between, tag, constant_pacing

# ── Courses scenario credentials ──────────────────────────────────────────────

_COURSES_FILE = Path(__file__).parent / "load_test_courses.json"
_courses_data = json.loads(_COURSES_FILE.read_text()) if _COURSES_FILE.exists() else {}

_course_students   = _courses_data.get("students", [])
_course_moderators = _courses_data.get("moderators", [])
_course_surveys    = _courses_data.get("surveys", [])

_course_student_lock = threading.Lock()
_course_student_idx  = 0

_course_moderator_lock = threading.Lock()
_course_moderator_idx  = 0


def _next_course_student():
    global _course_student_idx
    with _course_student_lock:
        cred = _course_students[_course_student_idx % len(_course_students)]
        _course_student_idx += 1
    return cred


def _next_course_moderator():
    global _course_moderator_idx
    with _course_moderator_lock:
        idx = _course_moderator_idx % len(_course_moderators)
        cred = _course_moderators[idx]
        survey = _course_surveys[idx]
        _course_moderator_idx += 1
    return cred, survey

# ── Credentials pool ──────────────────────────────────────────────────────────

_CREDS_FILE = Path(__file__).parent / "load_test_users.json"
_creds = json.loads(_CREDS_FILE.read_text()) if _CREDS_FILE.exists() else {"participants": [], "moderators": []}

_participants = _creds.get("participants", [])
_moderators   = _creds.get("moderators", [])

# Thread-safe index so each locust user picks a unique credential
_participant_lock = threading.Lock()
_participant_idx  = 0

_moderator_lock = threading.Lock()
_moderator_idx  = 0


def _next_participant():
    global _participant_idx
    with _participant_lock:
        cred = _participants[_participant_idx % len(_participants)]
        _participant_idx += 1
    return cred


def _next_moderator():
    global _moderator_idx
    with _moderator_lock:
        cred = _moderators[_moderator_idx % len(_moderators)]
        _moderator_idx += 1
    return cred


# ── Survey data (from live DB) ─────────────────────────────────────────────────

SURVEYS = [
    {
        "id": 1,
        "invite_code": "pw9AQDwS8msCvljlpoH1eQ",
        "ranking_mode": "ordinal",
        "item_ids": [1, 2, 3, 4, 5, 6],
    },
    {
        "id": 2,
        "invite_code": "AGcjWR3JzgjTfGGn6nubSg",
        "ranking_mode": "budget",
        "item_ids": list(range(7, 27)),  # 20 items
        "total_points": 1000,
    },
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _csrf(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("input", {"name": "csrf_token"})
    return tag["value"] if tag else ""


def _login(client, email: str, password: str) -> bool:
    resp = client.get("/login", name="/login [GET]")
    if resp.status_code != 200:
        return False
    token = _csrf(resp.text)
    resp = client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        allow_redirects=True,
        name="/login [POST]",
    )
    return resp.status_code == 200 and "logout" in resp.text.lower()


def _register(client, email: str, password: str) -> bool:
    resp = client.get("/register", name="/register [GET]")
    if resp.status_code != 200:
        return False
    token = _csrf(resp.text)
    username = email.split("@")[0]
    resp = client.post(
        "/register",
        data={
            "email": email,
            "password": password,
            "password_confirm": password,
            "username": username,
            "csrf_token": token,
        },
        allow_redirects=True,
        name="/register [POST]",
    )
    if resp.status_code != 200:
        return False
    if "logout" not in resp.text.lower():
        return _login(client, email, password)
    return True


def _ordinal_payload(item_ids: list, token: str) -> dict:
    ranks = list(range(1, len(item_ids) + 1))
    random.shuffle(ranks)
    data = {"csrf_token": token}
    for item_id, rank in zip(item_ids, ranks):
        data[f"rank_{item_id}"] = rank
    return data


def _budget_payload(item_ids: list, total_points: int, token: str) -> dict:
    n = len(item_ids)
    cuts = sorted(random.sample(range(0, total_points + 1), min(n - 1, total_points)))
    cuts = [0] + cuts + [total_points]
    points = [cuts[i + 1] - cuts[i] for i in range(n)]
    data = {"csrf_token": token}
    for item_id, pts in zip(item_ids, points):
        data[f"points_{item_id}"] = pts
    return data


def _rating_payload(item_ids: list, min_score: int, max_score: int, token: str) -> dict:
    data = {"csrf_token": token}
    for item_id in item_ids:
        data[f"rating_{item_id}"] = random.randint(min_score, max_score)
    return data


def _get_csrf(client, url: str, name: str) -> str:
    resp = client.get(url, name=name)
    return _csrf(resp.text) if resp.status_code == 200 else ""


# ── User classes ───────────────────────────────────────────────────────────────

class ReturningParticipant(HttpUser):
    """
    Pre-seeded user who logs in and interacts with a survey.
    Represents the large majority of real traffic.
    """
    weight = 9
    wait_time = between(2, 8)

    def on_start(self):
        cred = _next_participant()
        self.email = cred["email"]
        self.password = cred["password"]
        self.survey = random.choice(SURVEYS)
        self.joined = False

        if not _login(self.client, self.email, self.password):
            self._logged_in = False
            self.environment.events.request.fire(
                request_type="LOGIN",
                name="on_start /login",
                response_time=0,
                response_length=0,
                exception=Exception(f"Login failed for {self.email}"),
                context={},
            )
        else:
            self._logged_in = True

    @tag("participant", "join")
    @task(1)
    def join_survey(self):
        if not self._logged_in or self.joined:
            return
        resp = self.client.get(
            f"/join/{self.survey['invite_code']}",
            allow_redirects=True,
            name="/join/[invite_code]",
        )
        if resp.status_code == 200:
            self.joined = True

    @tag("participant", "read")
    @task(4)
    def view_rank_page(self):
        if not self._logged_in or not self.joined:
            self.join_survey()
            return
        self.client.get(
            f"/surveys/{self.survey['id']}/rank",
            name="/surveys/[id]/rank [GET]",
        )

    @tag("participant", "write")
    @task(2)
    def submit_ranking(self):
        if not self._logged_in or not self.joined:
            self.join_survey()
            return
        sid = self.survey["id"]
        token = _get_csrf(
            self.client,
            f"/surveys/{sid}/rank",
            name="/surveys/[id]/rank [GET csrf]",
        )
        if not token:
            return

        mode = self.survey["ranking_mode"]
        item_ids = self.survey["item_ids"]

        if mode == "ordinal":
            data = _ordinal_payload(item_ids, token)
        elif mode in ("budget", "points"):
            data = _budget_payload(item_ids, self.survey["total_points"], token)
        else:  # rating
            data = _rating_payload(
                item_ids,
                self.survey.get("min_score", 1),
                self.survey.get("max_score", 10),
                token,
            )

        self.client.post(
            f"/surveys/{sid}/rank",
            data=data,
            allow_redirects=True,
            name="/surveys/[id]/rank [POST]",
        )

    @tag("participant", "read")
    @task(1)
    def view_my_results(self):
        if not self._logged_in or not self.joined:
            return
        self.client.get(
            f"/surveys/{self.survey['id']}/my-results",
            name="/surveys/[id]/my-results",
        )

    @tag("participant", "read")
    @task(1)
    def view_my_surveys(self):
        if not self._logged_in:
            return
        self.client.get("/my-surveys", name="/my-surveys")


class NewParticipant(HttpUser):
    """
    Fresh user who registers during the test — simulates organic sign-ups.
    Kept at 10% of traffic so bcrypt cost doesn't dominate.
    """
    weight = 1
    wait_time = between(3, 10)

    def on_start(self):
        uid = random.randint(10_000_000, 99_999_999)
        self.email = f"new_user_{uid}@test.local"
        self.password = "Newuser#123"
        self.survey = random.choice(SURVEYS)
        self.joined = False

        if not _register(self.client, self.email, self.password):
            self.environment.runner.quit()

    # Reuse same tasks as ReturningParticipant

    @tag("new", "join")
    @task(2)
    def join_survey(self):
        if self.joined:
            return
        resp = self.client.get(
            f"/join/{self.survey['invite_code']}",
            allow_redirects=True,
            name="/join/[invite_code]",
        )
        if resp.status_code == 200:
            self.joined = True

    @tag("new", "read")
    @task(3)
    def view_rank_page(self):
        if not self.joined:
            self.join_survey()
            return
        self.client.get(
            f"/surveys/{self.survey['id']}/rank",
            name="/surveys/[id]/rank [GET]",
        )

    @tag("new", "write")
    @task(2)
    def submit_ranking(self):
        if not self.joined:
            self.join_survey()
            return
        sid = self.survey["id"]
        token = _get_csrf(
            self.client,
            f"/surveys/{sid}/rank",
            name="/surveys/[id]/rank [GET csrf]",
        )
        if not token:
            return

        mode = self.survey["ranking_mode"]
        item_ids = self.survey["item_ids"]

        if mode == "ordinal":
            data = _ordinal_payload(item_ids, token)
        else:
            data = _budget_payload(item_ids, self.survey["total_points"], token)

        self.client.post(
            f"/surveys/{sid}/rank",
            data=data,
            allow_redirects=True,
            name="/surveys/[id]/rank [POST]",
        )

    @tag("new", "read")
    @task(1)
    def view_my_surveys(self):
        self.client.get("/my-surveys", name="/my-surveys")


class ModeratorUser(HttpUser):
    """
    Pre-seeded moderator browsing the admin/management pages.
    Low count, high think time — mimics real moderator behaviour.
    """
    weight = 0  # spawned via fixed_count below
    fixed_count = 5
    wait_time = between(5, 15)

    def on_start(self):
        cred = _next_moderator()
        if not _login(self.client, cred["email"], cred["password"]):
            self.environment.runner.quit()
        self.survey = random.choice(SURVEYS)

    @tag("moderator", "read")
    @task(3)
    def view_survey_edit(self):
        self.client.get(
            f"/surveys/{self.survey['id']}",
            name="/surveys/[id] [edit]",
        )

    @tag("moderator", "read")
    @task(2)
    def view_results(self):
        self.client.get(
            f"/surveys/{self.survey['id']}/results",
            name="/surveys/[id]/results",
        )

    @tag("moderator", "read")
    @task(1)
    def view_survey_list(self):
        self.client.get("/surveys", name="/surveys")


# ── Courses scenario ───────────────────────────────────────────────────────────

class CourseStudent(HttpUser):
    """
    1000 pre-seeded students. Each picks one of the 10 course surveys,
    joins it, and submits budget rankings with a random capacity (1–6).

    Run the full courses scenario (students + moderators) with:
        locust -f tests/locustfile.py --host=http://localhost:5032 \\
            --headless -u 1015 -r 20 -t 10m --only-summary --tags courses
        (-u 1015 = 1000 students + 10 moderators + 5 from ModeratorUser fixed_count)
    """
    weight = 0
    fixed_count = 1000
    wait_time = between(1, 5)

    def on_start(self):
        cred = _next_course_student()
        self.email = cred["email"]
        self.password = cred["password"]
        self.survey = random.choice(_course_surveys)
        self.user_capacity = random.randint(1, 6)
        self.joined = False
        self._logged_in = _login(self.client, self.email, self.password)

    @tag("courses")
    @task(1)
    def join_survey(self):
        if not self._logged_in or self.joined:
            return
        resp = self.client.get(
            f"/join/{self.survey['invite_code']}",
            allow_redirects=True,
            name="/join/[invite_code] [courses]",
        )
        if resp.status_code == 200:
            self.joined = True

    @tag("courses")
    @task(3)
    def submit_ranking(self):
        if not self._logged_in:
            return
        if not self.joined:
            self.join_survey()
            return
        sid = self.survey["id"]
        token = _get_csrf(
            self.client,
            f"/surveys/{sid}/rank",
            name="/surveys/[id]/rank [GET csrf] [courses]",
        )
        if not token:
            return
        data = _budget_payload(self.survey["item_ids"], self.survey["total_points"], token)
        data["user_capacity"] = self.user_capacity
        self.client.post(
            f"/surveys/{sid}/rank",
            data=data,
            allow_redirects=True,
            name="/surveys/[id]/rank [POST] [courses]",
        )

    @tag("courses")
    @task(1)
    def view_my_results(self):
        if not self._logged_in or not self.joined:
            return
        self.client.get(
            f"/surveys/{self.survey['id']}/my-results",
            name="/surveys/[id]/my-results [courses]",
        )


class CourseModeratorUser(HttpUser):
    """
    10 pre-seeded moderators, each owning one course survey.
    They run iterated_maximum_matching in parallel — one moderator per survey.
    """
    weight = 0
    fixed_count = 10
    wait_time = between(30, 60)

    def on_start(self):
        cred, survey = _next_course_moderator()
        self.survey = survey
        if not _login(self.client, cred["email"], cred["password"]):
            self.environment.runner.quit()

    @tag("courses")
    @task(1)
    def run_algorithm(self):
        sid = self.survey["id"]
        token = _get_csrf(
            self.client,
            f"/surveys/{sid}/edit",
            name="/surveys/[id]/edit [GET csrf] [course-mod]",
        )
        if not token:
            return
        self.client.post(
            f"/surveys/{sid}/run-algorithm",
            data={"algorithm": "iterated_maximum_matching", "csrf_token": token},
            allow_redirects=True,
            name="/surveys/[id]/run-algorithm [courses]",
        )

    @tag("courses")
    @task(2)
    def view_results(self):
        self.client.get(
            f"/surveys/{self.survey['id']}/results",
            name="/surveys/[id]/results [course-mod]",
        )
