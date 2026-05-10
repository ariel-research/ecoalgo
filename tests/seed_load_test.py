"""
Seed script for load testing.

Default scenario:
  Creates:
    - 450 participant users  (loadtest_p_001@test.local … loadtest_p_450@test.local)
    -   5 moderator users    (loadtest_mod_1@test.local … loadtest_mod_5@test.local)
  Saves credentials to tests/load_test_users.json

Courses scenario (--scenario courses):
  Creates:
    - 1000 student users     (loadtest_cs_0001@test.local … loadtest_cs_1000@test.local)
    -  10 moderator users    (loadtest_cm_1@test.local  … loadtest_cm_10@test.local)
    -  10 budget surveys (one per moderator), each with 20 course items (capacity 40)
  Saves credentials + survey info to tests/load_test_courses.json

Usage:
    python tests/seed_load_test.py                        # default scenario
    python tests/seed_load_test.py --scenario courses     # courses scenario
    python tests/seed_load_test.py --clean                # remove default loadtest_* users
    python tests/seed_load_test.py --scenario courses --clean
"""

import sys
import json
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import app, db, user_datastore, create_roles
from flask_security import hash_password
from models import Survey, SurveyItem

CREDENTIALS_FILE = Path(__file__).parent / "load_test_users.json"
COURSES_FILE     = Path(__file__).parent / "load_test_courses.json"
PASSWORD = "Loadtest#123"

# ── Default scenario constants ────────────────────────────────────────────────
NUM_PARTICIPANTS = 450
NUM_MODERATORS   = 5

# ── Courses scenario constants ────────────────────────────────────────────────
NUM_COURSE_STUDENTS   = 1000
NUM_COURSE_MODERATORS = 10
NUM_ITEMS_PER_SURVEY  = 20
ITEM_CAPACITY         = 40
TOTAL_POINTS          = 1000  # budget points per student


# ── Default scenario ──────────────────────────────────────────────────────────

def seed():
    participants = []
    moderators = []

    hashed = hash_password(PASSWORD)

    print(f"Seeding {NUM_PARTICIPANTS} participants...")
    for i in range(1, NUM_PARTICIPANTS + 1):
        email = f"loadtest_p_{i:03d}@test.local"
        username = f"loadtest_p_{i:03d}"

        existing = user_datastore.find_user(email=email)
        if existing:
            participants.append({"email": email, "password": PASSWORD})
            continue

        u = user_datastore.create_user(
            email=email,
            username=username,
            password=hashed,
            fs_uniquifier=str(uuid.uuid4()),
            active=True,
        )
        role = user_datastore.find_role("user")
        if role:
            user_datastore.add_role_to_user(u, role)
        participants.append({"email": email, "password": PASSWORD})

        if i % 50 == 0:
            db.session.commit()
            print(f"  {i}/{NUM_PARTICIPANTS}")

    db.session.commit()

    print(f"Seeding {NUM_MODERATORS} moderators...")
    for i in range(1, NUM_MODERATORS + 1):
        email = f"loadtest_mod_{i}@test.local"
        username = f"loadtest_mod_{i}"

        existing = user_datastore.find_user(email=email)
        if existing:
            moderators.append({"email": email, "password": PASSWORD})
            continue

        u = user_datastore.create_user(
            email=email,
            username=username,
            password=hashed,
            fs_uniquifier=str(uuid.uuid4()),
            active=True,
        )
        for role_name in ("user", "moderator"):
            role = user_datastore.find_role(role_name)
            if role:
                user_datastore.add_role_to_user(u, role)
        moderators.append({"email": email, "password": PASSWORD})

    db.session.commit()

    credentials = {"participants": participants, "moderators": moderators}
    CREDENTIALS_FILE.write_text(json.dumps(credentials, indent=2))
    print(f"\nDone. Credentials saved to {CREDENTIALS_FILE}")
    print(f"  {len(participants)} participants, {len(moderators)} moderators")


def clean():
    from models import User
    deleted = User.query.filter(User.email.like("loadtest_%")).delete()
    db.session.commit()
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()
    print(f"Removed {deleted} loadtest users and credentials file.")


# ── Courses scenario ──────────────────────────────────────────────────────────

def seed_courses():
    hashed = hash_password(PASSWORD)
    students = []
    moderators = []
    surveys_out = []

    print(f"Seeding {NUM_COURSE_STUDENTS} course students...")
    for i in range(1, NUM_COURSE_STUDENTS + 1):
        email = f"loadtest_cs_{i:04d}@test.local"
        username = f"loadtest_cs_{i:04d}"

        existing = user_datastore.find_user(email=email)
        if not existing:
            u = user_datastore.create_user(
                email=email,
                username=username,
                password=hashed,
                fs_uniquifier=str(uuid.uuid4()),
                active=True,
            )
            role = user_datastore.find_role("user")
            if role:
                user_datastore.add_role_to_user(u, role)

        students.append({"email": email, "password": PASSWORD})

        if i % 100 == 0:
            db.session.commit()
            print(f"  {i}/{NUM_COURSE_STUDENTS}")

    db.session.commit()

    print(f"Seeding {NUM_COURSE_MODERATORS} course moderators + surveys...")
    for i in range(1, NUM_COURSE_MODERATORS + 1):
        email = f"loadtest_cm_{i}@test.local"
        username = f"loadtest_cm_{i}"

        existing = user_datastore.find_user(email=email)
        if existing:
            mod_user = existing
        else:
            mod_user = user_datastore.create_user(
                email=email,
                username=username,
                password=hashed,
                fs_uniquifier=str(uuid.uuid4()),
                active=True,
            )
            for role_name in ("user", "moderator"):
                role = user_datastore.find_role(role_name)
                if role:
                    user_datastore.add_role_to_user(mod_user, role)
            db.session.commit()

        moderators.append({"email": email, "password": PASSWORD, "index": i})

        # Create survey for this moderator if it doesn't already exist
        existing_survey = Survey.query.filter_by(
            creator_id=mod_user.id, title=f"Course Survey {i}"
        ).first()

        if existing_survey:
            survey = existing_survey
            item_ids = [item.id for item in survey.items.all()]
        else:
            survey = Survey(
                title=f"Course Survey {i}",
                description=f"Load test budget survey for moderator {i}",
                creator_id=mod_user.id,
                ranking_mode="budget",
                total_points=TOTAL_POINTS,
                use_item_capacity=True,
                require_user_capacity=True,
                is_open=True,
            )
            db.session.add(survey)
            db.session.flush()  # get survey.id before adding items

            item_ids = []
            for j in range(1, NUM_ITEMS_PER_SURVEY + 1):
                item = SurveyItem(
                    survey_id=survey.id,
                    name=f"Course {j}",
                    capacity=ITEM_CAPACITY,
                )
                db.session.add(item)
                db.session.flush()
                item_ids.append(item.id)

            db.session.commit()

        surveys_out.append({
            "id": survey.id,
            "invite_code": survey.invite_code,
            "moderator_email": email,
            "ranking_mode": "budget",
            "total_points": TOTAL_POINTS,
            "item_ids": item_ids,
        })

        print(f"  Moderator {i}: survey id={survey.id}, invite={survey.invite_code}")

    db.session.commit()

    output = {
        "students": students,
        "moderators": moderators,
        "surveys": surveys_out,
    }
    COURSES_FILE.write_text(json.dumps(output, indent=2))
    print(f"\nDone. Data saved to {COURSES_FILE}")
    print(f"  {len(students)} students, {len(moderators)} moderators, {len(surveys_out)} surveys")


def clean_courses():
    from models import User
    deleted = User.query.filter(
        User.email.like("loadtest_cs_%") | User.email.like("loadtest_cm_%")
    ).delete(synchronize_session=False)
    db.session.commit()
    if COURSES_FILE.exists():
        COURSES_FILE.unlink()
    print(f"Removed {deleted} course loadtest users and data file.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scenario = "default"
    if "--scenario" in sys.argv:
        idx = sys.argv.index("--scenario")
        if idx + 1 < len(sys.argv):
            scenario = sys.argv[idx + 1]

    with app.app_context():
        create_roles()
        if "--clean" in sys.argv:
            if scenario == "courses":
                clean_courses()
            else:
                clean()
        else:
            if scenario == "courses":
                seed_courses()
            else:
                seed()
