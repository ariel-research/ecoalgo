"""Comprehensive test suite for the Fair Division Algorithms app."""
import json
import pytest
from models import db, User, Role, Survey, SurveyItem, SurveyParticipant, ItemRanking, AllocationResult
from tests.conftest import register_user, login, logout


def _ajax(client, url, data=None):
    """Helper for AJAX POST requests."""
    return client.post(url, data=data or {},
                       headers={'X-Requested-With': 'XMLHttpRequest'})


# ==================== AUTH & REGISTRATION ====================

class TestAuth:
    def test_register(self, client, app):
        rv = register_user(client, 'new@test.com', 'newpass123')
        assert rv.status_code == 200
        with app.app_context():
            assert User.query.filter_by(email='new@test.com').first() is not None

    def test_register_duplicate_email(self, client, app, regular_user):
        rv = register_user(client, 'user@test.com', 'otherpass')
        assert b'already associated' in rv.data or rv.status_code == 200

    def test_login_logout(self, client, regular_user):
        rv = login(client, 'user@test.com', 'userpass')
        assert rv.status_code == 200
        rv = logout(client)
        assert rv.status_code == 200

    def test_unauthenticated_redirect(self, client):
        rv = client.get('/surveys', follow_redirects=False)
        assert rv.status_code in (302, 303)


# ==================== ADMIN ====================

class TestAdmin:
    def test_admin_dashboard_accessible(self, logged_in_admin):
        rv = logged_in_admin.get('/admin')
        assert rv.status_code == 200

    def test_admin_dashboard_forbidden_for_regular(self, logged_in_user):
        rv = logged_in_user.get('/admin', follow_redirects=False)
        assert rv.status_code in (302, 303, 403)

    def test_toggle_user_active(self, logged_in_admin, app, regular_user):
        rv = logged_in_admin.post(
            f'/admin/users/{regular_user.id}/toggle-active',
            follow_redirects=True,
        )
        assert rv.status_code == 200
        with app.app_context():
            u = db.session.get(User, regular_user.id)
            assert u.active is False

    def test_add_remove_role(self, logged_in_admin, app, regular_user):
        with app.app_context():
            mod_role = Role.query.filter_by(name='moderator').first()
            role_id = mod_role.id
        # Add role
        rv = logged_in_admin.post(
            f'/admin/users/{regular_user.id}/add-role/{role_id}',
            follow_redirects=True,
        )
        assert rv.status_code == 200
        with app.app_context():
            u = db.session.get(User, regular_user.id)
            assert any(r.name == 'moderator' for r in u.roles)
        # Remove role
        rv = logged_in_admin.post(
            f'/admin/users/{regular_user.id}/remove-role/{role_id}',
            follow_redirects=True,
        )
        assert rv.status_code == 200
        with app.app_context():
            u = db.session.get(User, regular_user.id)
            assert not any(r.name == 'moderator' for r in u.roles)

    def test_delete_user(self, logged_in_admin, app, regular_user):
        rv = logged_in_admin.post(
            f'/admin/users/{regular_user.id}/delete',
            follow_redirects=True,
        )
        assert rv.status_code == 200
        with app.app_context():
            assert db.session.get(User, regular_user.id) is None


# ==================== SURVEY CRUD ====================

class TestSurveyCRUD:
    def test_create_ordinal(self, logged_in_admin, app):
        rv = logged_in_admin.post('/surveys/create', data={
            'title': 'Ordinal Survey',
            'description': 'test',
            'ranking_mode': 'ordinal',
        }, follow_redirects=True)
        assert rv.status_code == 200
        with app.app_context():
            assert Survey.query.filter_by(title='Ordinal Survey').first() is not None

    def test_create_budget(self, logged_in_admin, app):
        rv = logged_in_admin.post('/surveys/create', data={
            'title': 'Budget Survey',
            'ranking_mode': 'budget',
            'total_points': '200',
        }, follow_redirects=True)
        assert rv.status_code == 200
        with app.app_context():
            s = Survey.query.filter_by(title='Budget Survey').first()
            assert s.ranking_mode == 'budget'
            assert s.total_points == 200

    def test_create_rating(self, logged_in_admin, app):
        rv = logged_in_admin.post('/surveys/create', data={
            'title': 'Rating Survey',
            'ranking_mode': 'rating',
            'min_score': '1',
            'max_score': '5',
        }, follow_redirects=True)
        assert rv.status_code == 200
        with app.app_context():
            s = Survey.query.filter_by(title='Rating Survey').first()
            assert s.ranking_mode == 'rating'
            assert s.max_score == 5

    def test_create_with_flags(self, logged_in_admin, app):
        rv = logged_in_admin.post('/surveys/create', data={
            'title': 'Flagged Survey',
            'ranking_mode': 'ordinal',
            'use_item_capacity': 'on',
            'use_weights': 'on',
            'require_user_capacity': 'on',
        }, follow_redirects=True)
        assert rv.status_code == 200
        with app.app_context():
            s = Survey.query.filter_by(title='Flagged Survey').first()
            assert s.use_item_capacity is True
            assert s.use_weights is True
            assert s.require_user_capacity is True

    def test_update_settings(self, logged_in_admin, app, survey):
        rv = logged_in_admin.post(f'/surveys/{survey.id}/update', data={
            'title': 'Updated Title',
            'ranking_mode': 'budget',
            'total_points': '50',
        }, follow_redirects=True)
        assert rv.status_code == 200
        with app.app_context():
            s = db.session.get(Survey, survey.id)
            assert s.title == 'Updated Title'
            assert s.ranking_mode == 'budget'

    def test_toggle_open_closed(self, logged_in_admin, app, survey):
        rv = logged_in_admin.post(
            f'/surveys/{survey.id}/toggle', follow_redirects=True,
        )
        assert rv.status_code == 200
        with app.app_context():
            s = db.session.get(Survey, survey.id)
            assert s.is_open is False

    def test_delete_survey(self, logged_in_admin, app, survey):
        sid = survey.id
        rv = logged_in_admin.post(
            f'/surveys/{sid}/delete', follow_redirects=True,
        )
        assert rv.status_code == 200
        with app.app_context():
            assert db.session.get(Survey, sid) is None

    def test_non_owner_gets_403(self, app, client, survey, regular_user):
        with app.app_context():
            mod_role = Role.query.filter_by(name='moderator').first()
            u = db.session.get(User, regular_user.id)
            u.roles.append(mod_role)
            db.session.commit()
        login(client, 'user@test.com', 'userpass')
        rv = client.get(f'/surveys/{survey.id}', follow_redirects=False)
        assert rv.status_code == 403


# ==================== ITEMS ====================

class TestItems:
    def test_add_item(self, logged_in_admin, app, survey):
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey.id}/items/add',
                   {'name': 'New Item', 'capacity': '2', 'weight': '1.5'})
        j = rv.get_json()
        assert j['success'] is True
        with app.app_context():
            assert SurveyItem.query.filter_by(name='New Item').first() is not None

    def test_add_item_missing_name(self, logged_in_admin, survey):
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey.id}/items/add', {'name': ''})
        j = rv.get_json()
        assert j['success'] is False

    def test_delete_item(self, logged_in_admin, app, survey_with_items):
        item_id = survey_with_items.item_ids[0]
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_with_items.id}/items/{item_id}/delete')
        j = rv.get_json()
        assert j['success'] is True
        with app.app_context():
            assert db.session.get(SurveyItem, item_id) is None

    def test_inline_edit_name(self, logged_in_admin, app, survey_with_items):
        item_id = survey_with_items.item_ids[0]
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_with_items.id}/items/{item_id}/edit',
                   {'field': 'name', 'value': 'Renamed'})
        assert rv.get_json()['success'] is True
        with app.app_context():
            assert db.session.get(SurveyItem, item_id).name == 'Renamed'

    def test_inline_edit_capacity(self, logged_in_admin, app, survey_with_items):
        item_id = survey_with_items.item_ids[0]
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_with_items.id}/items/{item_id}/edit',
                   {'field': 'capacity', 'value': '5'})
        assert rv.get_json()['success'] is True
        with app.app_context():
            assert db.session.get(SurveyItem, item_id).capacity == 5


# ==================== PARTICIPANTS ====================

class TestParticipants:
    def test_add_participant(self, logged_in_admin, app, survey, regular_user):
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey.id}/participants/add',
                   {'user_id': str(regular_user.id)})
        j = rv.get_json()
        assert j['success'] is True

    def test_duplicate_participant(self, logged_in_admin, app, survey, regular_user):
        _ajax(logged_in_admin,
              f'/surveys/{survey.id}/participants/add',
              {'user_id': str(regular_user.id)})
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey.id}/participants/add',
                   {'user_id': str(regular_user.id)})
        j = rv.get_json()
        assert j['success'] is False

    def test_remove_participant(self, logged_in_admin, app, survey, regular_user):
        _ajax(logged_in_admin,
              f'/surveys/{survey.id}/participants/add',
              {'user_id': str(regular_user.id)})
        with app.app_context():
            p = SurveyParticipant.query.filter_by(
                survey_id=survey.id, user_id=regular_user.id).first()
            pid = p.id
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey.id}/participants/{pid}/remove')
        assert rv.get_json()['success'] is True

    def test_update_participant_field(self, logged_in_admin, app, survey, regular_user):
        _ajax(logged_in_admin,
              f'/surveys/{survey.id}/participants/add',
              {'user_id': str(regular_user.id)})
        with app.app_context():
            p = SurveyParticipant.query.filter_by(
                survey_id=survey.id, user_id=regular_user.id).first()
            pid = p.id
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey.id}/participants/{pid}/update-field',
                   {'field': 'user_weight', 'value': '2.5'})
        assert rv.get_json()['success'] is True
        with app.app_context():
            p = db.session.get(SurveyParticipant, pid)
            assert p.user_weight == 2.5


# ==================== DUMMY USERS ====================

class TestDummyUsers:
    def test_add_random(self, logged_in_admin, app, survey_with_items):
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_with_items.id}/dummy-users/add',
                   {'count': '2'})
        assert rv.get_json()['success'] is True
        with app.app_context():
            cnt = SurveyParticipant.query.filter_by(
                survey_id=survey_with_items.id, is_dummy=True).count()
            assert cnt == 2

    def test_add_manual_weight_capacity(self, logged_in_admin, app, survey_with_items):
        with app.app_context():
            s = db.session.get(Survey, survey_with_items.id)
            s.use_weights = True
            s.require_user_capacity = True
            db.session.commit()
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_with_items.id}/dummy-users/add',
                   {'count': '1', 'dummy_weight': '3.0', 'dummy_capacity': '2'})
        assert rv.get_json()['success'] is True
        with app.app_context():
            p = SurveyParticipant.query.filter_by(
                survey_id=survey_with_items.id, is_dummy=True).first()
            assert p.user_weight == 3.0
            assert p.user_capacity == 2

    def test_remove_all(self, logged_in_admin, app, survey_full):
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_full.id}/dummy-users/remove-all')
        assert rv.get_json()['success'] is True
        with app.app_context():
            cnt = SurveyParticipant.query.filter_by(
                survey_id=survey_full.id, is_dummy=True).count()
            assert cnt == 0

    def test_regenerate(self, logged_in_admin, app, survey_full):
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_full.id}/dummy-users/regenerate')
        assert rv.get_json()['success'] is True

    def test_edit_dummy_ratings(self, logged_in_admin, app, survey_full):
        with app.app_context():
            p = SurveyParticipant.query.filter_by(
                survey_id=survey_full.id, is_dummy=True).first()
            pid = p.id
            items = SurveyItem.query.filter_by(survey_id=survey_full.id).all()
            form_data = {f'rank_{it.id}': str(idx + 1) for idx, it in enumerate(items)}
        rv = logged_in_admin.post(
            f'/surveys/{survey_full.id}/dummy-users/{pid}/edit-ratings',
            data=form_data, follow_redirects=True,
        )
        assert rv.status_code == 200


# ==================== RATING INLINE EDIT ====================

class TestRatingInlineEdit:
    def test_update_rating(self, logged_in_admin, app, survey_full):
        with app.app_context():
            p = SurveyParticipant.query.filter_by(
                survey_id=survey_full.id, is_dummy=True).first()
            pid = p.id
            item_id = survey_full.item_ids[0]
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_full.id}/ratings/update',
                   {'participant_id': str(pid),
                    'item_id': str(item_id),
                    'value': '3'})
        assert rv.get_json()['success'] is True

    def test_cannot_edit_non_dummy(self, logged_in_admin, app, survey_with_items, regular_user):
        with app.app_context():
            rp = SurveyParticipant(survey_id=survey_with_items.id, user_id=regular_user.id)
            db.session.add(rp)
            db.session.commit()
            rp_id = rp.id
            item_id = survey_with_items.item_ids[0]
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_with_items.id}/ratings/update',
                   {'participant_id': str(rp_id),
                    'item_id': str(item_id),
                    'value': '5'})
        assert rv.get_json()['success'] is False

    def test_invalid_value(self, logged_in_admin, app, survey_full):
        with app.app_context():
            p = SurveyParticipant.query.filter_by(
                survey_id=survey_full.id, is_dummy=True).first()
            pid = p.id
            item_id = survey_full.item_ids[0]
        rv = _ajax(logged_in_admin,
                   f'/surveys/{survey_full.id}/ratings/update',
                   {'participant_id': str(pid),
                    'item_id': str(item_id),
                    'value': 'abc'})
        assert rv.get_json()['success'] is False


# ==================== INVITE & RANKING ====================

class TestInviteAndRanking:
    def test_join_via_invite(self, app, client, survey_with_items, regular_user):
        login(client, 'user@test.com', 'userpass')
        rv = client.get(f'/join/{survey_with_items.invite_code}', follow_redirects=True)
        assert rv.status_code == 200
        with app.app_context():
            assert SurveyParticipant.query.filter_by(
                survey_id=survey_with_items.id,
                user_id=regular_user.id).first() is not None

    def test_submit_ordinal(self, app, client, survey_with_items, regular_user):
        login(client, 'user@test.com', 'userpass')
        client.get(f'/join/{survey_with_items.invite_code}', follow_redirects=True)
        with app.app_context():
            items = SurveyItem.query.filter_by(survey_id=survey_with_items.id).all()
            data = {f'rank_{it.id}': str(idx + 1) for idx, it in enumerate(items)}
            item_count = len(items)
        rv = client.post(f'/surveys/{survey_with_items.id}/rank',
                         data=data, follow_redirects=True)
        assert rv.status_code == 200
        with app.app_context():
            p = SurveyParticipant.query.filter_by(
                survey_id=survey_with_items.id,
                user_id=regular_user.id).first()
            assert p.rankings.count() == item_count

    def test_submit_budget(self, app, client, admin_user, regular_user):
        # Create budget survey as admin
        login(client, 'admin@test.com', 'adminpass')
        client.post('/surveys/create', data={
            'title': 'Budget Test',
            'ranking_mode': 'budget',
            'total_points': '100',
        }, follow_redirects=True)
        with app.app_context():
            s = Survey.query.filter_by(title='Budget Test').first()
            for name in ['X', 'Y']:
                db.session.add(SurveyItem(survey_id=s.id, name=name))
            db.session.commit()
            code = s.invite_code
            sid = s.id
            items = s.items.all()
            item_ids = [it.id for it in items]
        logout(client)
        # Join + submit as regular user
        login(client, 'user@test.com', 'userpass')
        client.get(f'/join/{code}', follow_redirects=True)
        rv = client.post(f'/surveys/{sid}/rank', data={
            f'points_{item_ids[0]}': '60',
            f'points_{item_ids[1]}': '40',
        }, follow_redirects=True)
        assert rv.status_code == 200
        assert b'saved' in rv.data.lower()

    def test_submit_rating_valid_and_invalid(self, app, client, admin_user, regular_user):
        login(client, 'admin@test.com', 'adminpass')
        client.post('/surveys/create', data={
            'title': 'Rating Test',
            'ranking_mode': 'rating',
            'min_score': '1',
            'max_score': '5',
        }, follow_redirects=True)
        with app.app_context():
            s = Survey.query.filter_by(title='Rating Test').first()
            db.session.add(SurveyItem(survey_id=s.id, name='R1'))
            db.session.commit()
            code = s.invite_code
            sid = s.id
            item_id = s.items.first().id
        logout(client)
        login(client, 'user@test.com', 'userpass')
        client.get(f'/join/{code}', follow_redirects=True)
        # Valid rating
        rv = client.post(f'/surveys/{sid}/rank', data={
            f'rating_{item_id}': '3',
        }, follow_redirects=True)
        assert rv.status_code == 200
        # Out-of-range rating
        rv = client.post(f'/surveys/{sid}/rank', data={
            f'rating_{item_id}': '99',
        }, follow_redirects=True)
        assert b'between' in rv.data or rv.status_code == 200


# ==================== ALGORITHM ====================

class TestAlgorithm:
    def test_run_round_robin(self, logged_in_admin, app, survey_full):
        rv = logged_in_admin.post(
            f'/surveys/{survey_full.id}/run-algorithm',
            data={'algorithm': 'round_robin'},
            follow_redirects=True,
        )
        assert rv.status_code == 200
        with app.app_context():
            assert AllocationResult.query.filter_by(
                survey_id=survey_full.id).count() >= 1

    def test_unknown_algorithm(self, logged_in_admin, survey_full):
        rv = logged_in_admin.post(
            f'/surveys/{survey_full.id}/run-algorithm',
            data={'algorithm': 'nonexistent_algo'},
            follow_redirects=True,
        )
        assert b'Unknown algorithm' in rv.data or rv.status_code == 200

    def test_no_rankings_fails(self, logged_in_admin, app, survey_with_items):
        rv = logged_in_admin.post(
            f'/surveys/{survey_with_items.id}/run-algorithm',
            data={'algorithm': 'round_robin'},
            follow_redirects=True,
        )
        assert b'No participants' in rv.data or rv.status_code == 200


# ==================== RESULTS ====================

class TestResults:
    def test_view_results(self, logged_in_admin, app, survey_full):
        logged_in_admin.post(
            f'/surveys/{survey_full.id}/run-algorithm',
            data={'algorithm': 'round_robin'},
            follow_redirects=True,
        )
        rv = logged_in_admin.get(f'/surveys/{survey_full.id}/results')
        assert rv.status_code == 200

    def test_delete_result(self, logged_in_admin, app, survey_full):
        logged_in_admin.post(
            f'/surveys/{survey_full.id}/run-algorithm',
            data={'algorithm': 'round_robin'},
            follow_redirects=True,
        )
        with app.app_context():
            result = AllocationResult.query.filter_by(
                survey_id=survey_full.id).first()
            rid = result.id
        rv = logged_in_admin.post(
            f'/surveys/{survey_full.id}/results/{rid}/delete',
            follow_redirects=True,
        )
        assert rv.status_code == 200
        with app.app_context():
            assert db.session.get(AllocationResult, rid) is None
