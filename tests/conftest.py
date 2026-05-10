import pytest
import uuid
from app import app as _app, db as _db, user_datastore, create_roles
from models import User, Role, Survey, SurveyItem, SurveyParticipant, ItemRanking, AllocationResult
from flask_security import hash_password

# Store original config values to restore after tests
_ORIG_DB_URI = _app.config['SQLALCHEMY_DATABASE_URI']


@pytest.fixture()
def app():
    """Create a Flask app with test configuration."""
    _app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI='sqlite://',  # in-memory
        WTF_CSRF_ENABLED=False,
        SERVER_NAME='localhost',
    )
    with _app.app_context():
        _db.create_all()
        create_roles()
        yield _app
        _db.session.remove()
        _db.drop_all()
    # Restore original DB URI so the production db isn't affected
    _app.config['SQLALCHEMY_DATABASE_URI'] = _ORIG_DB_URI


@pytest.fixture()
def client(app):
    """Flask test client."""
    return app.test_client()


# ---- helpers ----

def register_user(client, email, password):
    return client.post('/register', data={
        'email': email,
        'password': password,
    }, follow_redirects=True)


def login(client, email, password):
    return client.post('/login', data={
        'email': email,
        'password': password,
    }, follow_redirects=True)


def logout(client):
    return client.get('/logout', follow_redirects=True)


def _make_user(app, email, password, roles=None):
    """Create a user directly in the DB. Returns simple object with id/email."""
    u = user_datastore.create_user(
        email=email,
        password=hash_password(password),
        username=email.split('@')[0],
        fs_uniquifier=str(uuid.uuid4()),
    )
    if roles:
        for r in roles:
            role = user_datastore.find_role(r)
            if role:
                user_datastore.add_role_to_user(u, role)
    _db.session.commit()
    uid = u.id
    return type('U', (), {'id': uid, 'email': email})()


@pytest.fixture()
def admin_user(app):
    return _make_user(app, 'admin@test.com', 'adminpass', ['admin'])


@pytest.fixture()
def regular_user(app):
    return _make_user(app, 'user@test.com', 'userpass', ['user'])


@pytest.fixture()
def logged_in_admin(client, admin_user):
    login(client, 'admin@test.com', 'adminpass')
    return client


@pytest.fixture()
def logged_in_user(client, regular_user):
    login(client, 'user@test.com', 'userpass')
    return client


@pytest.fixture()
def survey(app, admin_user):
    """A basic ordinal survey owned by admin."""
    s = Survey(
        title='Test Survey',
        description='A test',
        creator_id=admin_user.id,
        ranking_mode='ordinal',
    )
    _db.session.add(s)
    _db.session.commit()
    return type('S', (), {'id': s.id, 'invite_code': s.invite_code})()


@pytest.fixture()
def survey_with_items(app, survey):
    """Survey with 3 items."""
    item_ids = []
    for name in ['Item A', 'Item B', 'Item C']:
        it = SurveyItem(survey_id=survey.id, name=name)
        _db.session.add(it)
        _db.session.flush()
        item_ids.append(it.id)
    _db.session.commit()
    return type('S', (), {'id': survey.id, 'invite_code': survey.invite_code, 'item_ids': item_ids})()


@pytest.fixture()
def survey_full(app, survey_with_items):
    """Survey with items and 2 dummy participants that have ordinal rankings."""
    items = SurveyItem.query.filter_by(survey_id=survey_with_items.id).all()
    for i in range(1, 3):
        p = SurveyParticipant(
            survey_id=survey_with_items.id,
            is_dummy=True,
            dummy_name=f'Dummy {i}',
        )
        _db.session.add(p)
        _db.session.flush()
        for idx, item in enumerate(items):
            _db.session.add(ItemRanking(
                participant_id=p.id,
                item_id=item.id,
                rank=idx + 1,
            ))
    _db.session.commit()
    return type('S', (), {
        'id': survey_with_items.id,
        'invite_code': survey_with_items.invite_code,
        'item_ids': survey_with_items.item_ids,
    })()
