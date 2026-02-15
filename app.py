from flask import Flask, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_security import Security, SQLAlchemyUserDatastore, current_user, auth_required, roles_required, roles_accepted, hash_password, user_registered
from flask_wtf.csrf import CSRFProtect
from config import Config
from models import db, User, Role, Survey, SurveyItem, SurveyParticipant, ItemRanking, AllocationResult
from forms import ExtendedRegisterForm
import uuid
import json
import random


def is_ajax():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

# Initialize CSRF protection
csrf = CSRFProtect(app)

# Setup Flask-Security
user_datastore = SQLAlchemyUserDatastore(db, User, Role)
security = Security(app, user_datastore, register_form=ExtendedRegisterForm)


@user_registered.connect_via(app)
def on_user_registered(sender, user, **extra):
    """Auto-generate username from email when a user registers."""
    if not user.username:
        base = user.email.split('@')[0]
        username = base
        counter = 1
        while User.query.filter_by(username=username).first():
            username = f'{base}{counter}'
            counter += 1
        user.username = username
        db.session.commit()


def create_roles():
    """Create default roles if they don't exist."""
    roles = [
        {'name': 'admin', 'description': 'Administrator with full access'},
        {'name': 'user', 'description': 'Regular user'},
        {'name': 'moderator', 'description': 'Moderator with limited admin access'}
    ]
    for role_data in roles:
        if not user_datastore.find_role(role_data['name']):
            user_datastore.create_role(**role_data)
    db.session.commit()


def create_admin_user():
    """Create a default admin user if no admin exists."""
    admin_role = user_datastore.find_role('admin')
    if admin_role and not admin_role.users.first():
        admin = user_datastore.create_user(
            username='admin',
            email='admin@example.com',
            password=hash_password('adminpassword'),
            fs_uniquifier=str(uuid.uuid4()),
            roles=[admin_role]
        )
        db.session.commit()
        print("Default admin created: admin@example.com / adminpassword")


@app.route('/')
def home():
    return render_template('home.html')


@app.route('/admin')
@auth_required()
@roles_required('admin')
def admin_dashboard():
    """Admin dashboard - only accessible by admins."""
    users = User.query.all()
    roles = Role.query.all()
    return render_template('admin/dashboard.html', users=users, roles=roles)


@app.route('/admin/users')
@auth_required()
@roles_required('admin')
def admin_users():
    """User management page."""
    users = User.query.all()
    roles = Role.query.all()
    return render_template('admin/users.html', users=users, roles=roles)


@app.route('/admin/users/<int:user_id>/toggle-active', methods=['POST'])
@auth_required()
@roles_required('admin')
def toggle_user_active(user_id):
    """Toggle user active status."""
    user = User.query.get_or_404(user_id)
    if user == current_user:
        flash('You cannot deactivate your own account.', 'danger')
    else:
        user.active = not user.active
        db.session.commit()
        status = 'activated' if user.active else 'deactivated'
        flash(f'User {user.email} has been {status}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/add-role/<int:role_id>', methods=['POST'])
@auth_required()
@roles_required('admin')
def add_user_role(user_id, role_id):
    """Add a role to a user."""
    user = User.query.get_or_404(user_id)
    role = Role.query.get_or_404(role_id)
    if role not in user.roles:
        user.roles.append(role)
        db.session.commit()
        flash(f'Role {role.name} added to {user.email}.', 'success')
    else:
        flash(f'User already has role {role.name}.', 'info')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/remove-role/<int:role_id>', methods=['POST'])
@auth_required()
@roles_required('admin')
def remove_user_role(user_id, role_id):
    """Remove a role from a user."""
    user = User.query.get_or_404(user_id)
    role = Role.query.get_or_404(role_id)

    # Prevent removing own admin role
    if user == current_user and role.name == 'admin':
        flash('You cannot remove your own admin role.', 'danger')
    elif role in user.roles:
        user.roles.remove(role)
        db.session.commit()
        flash(f'Role {role.name} removed from {user.email}.', 'success')
    else:
        flash(f'User does not have role {role.name}.', 'info')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@auth_required()
@roles_required('admin')
def delete_user(user_id):
    """Delete a user."""
    user = User.query.get_or_404(user_id)
    if user == current_user:
        flash('You cannot delete your own account.', 'danger')
    else:
        email = user.email
        db.session.delete(user)
        db.session.commit()
        flash(f'User {email} has been deleted.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/moderator')
@auth_required()
@roles_accepted('admin', 'moderator')
def moderator_panel():
    """Moderator panel - accessible by admins and moderators."""
    surveys = Survey.query.filter_by(creator_id=current_user.id).all()
    return render_template('moderator/panel.html', surveys=surveys)


# ==================== SURVEY ROUTES ====================

@app.route('/surveys')
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_list():
    """List all surveys created by the current moderator."""
    surveys = Survey.query.filter_by(creator_id=current_user.id).all()
    return render_template('survey/list.html', surveys=surveys)


@app.route('/surveys/create', methods=['GET', 'POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_create():
    """Create a new survey."""
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        ranking_mode = request.form.get('ranking_mode', 'ordinal')
        total_points = request.form.get('total_points', 100, type=int)
        min_score = request.form.get('min_score', 1, type=int)
        max_score = request.form.get('max_score', 10, type=int)
        use_weights = request.form.get('use_weights') == 'on'
        require_user_capacity = request.form.get('require_user_capacity') == 'on'
        use_item_capacity = request.form.get('use_item_capacity') == 'on'

        if not title:
            flash('Survey title is required.', 'danger')
            return render_template('survey/create.html')

        survey = Survey(
            title=title,
            description=description,
            creator_id=current_user.id,
            ranking_mode=ranking_mode,
            total_points=total_points,
            min_score=min_score,
            max_score=max_score,
            use_weights=use_weights,
            require_user_capacity=require_user_capacity,
            use_item_capacity=use_item_capacity
        )
        db.session.add(survey)
        db.session.commit()

        flash(f'Survey "{title}" created successfully!', 'success')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    return render_template('survey/create.html')


@app.route('/surveys/<int:survey_id>')
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_edit(survey_id):
    """Edit survey details, manage items and participants."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    users = User.query.all()

    # Build rating matrix for the rating table
    items = survey.items.all()
    participants = survey.participants.all()
    rating_matrix = []
    for p in participants:
        row = {'participant': p, 'ratings': {}}
        for r in p.rankings.all():
            if survey.ranking_mode == 'ordinal':
                row['ratings'][r.item_id] = r.rank
            elif survey.ranking_mode in ('budget', 'points'):
                row['ratings'][r.item_id] = r.points
            else:
                row['ratings'][r.item_id] = r.rating
        rating_matrix.append(row)

    return render_template('survey/edit.html', survey=survey, users=users, items=items, rating_matrix=rating_matrix)


@app.route('/surveys/<int:survey_id>/update', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_update(survey_id):
    """Update survey settings."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    survey.title = request.form.get('title', survey.title)
    survey.description = request.form.get('description', survey.description)
    survey.ranking_mode = request.form.get('ranking_mode', survey.ranking_mode)
    survey.total_points = request.form.get('total_points', survey.total_points, type=int)
    survey.min_score = request.form.get('min_score', survey.min_score, type=int)
    survey.max_score = request.form.get('max_score', survey.max_score, type=int)
    survey.use_weights = request.form.get('use_weights') == 'on'
    survey.require_user_capacity = request.form.get('require_user_capacity') == 'on'
    survey.use_item_capacity = request.form.get('use_item_capacity') == 'on'

    db.session.commit()
    flash('Survey updated successfully!', 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/toggle', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_toggle(survey_id):
    """Toggle survey open/closed status."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    survey.is_open = not survey.is_open
    db.session.commit()

    status = 'opened' if survey.is_open else 'closed'
    flash(f'Survey has been {status}.', 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/delete', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_delete(survey_id):
    """Delete a survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    title = survey.title
    db.session.delete(survey)
    db.session.commit()

    flash(f'Survey "{title}" has been deleted.', 'success')
    return redirect(url_for('survey_list'))


# ==================== SURVEY ITEMS ====================

@app.route('/surveys/<int:survey_id>/items/add', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_add_item(survey_id):
    """Add an item to the survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    name = request.form.get('name')
    description = request.form.get('description')
    capacity = request.form.get('capacity', 1, type=int)
    weight = request.form.get('weight', 1.0, type=float)

    if not name:
        if is_ajax():
            return jsonify(success=False, message='Item name is required.')
        flash('Item name is required.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    item = SurveyItem(
        survey_id=survey.id,
        name=name,
        description=description,
        capacity=capacity,
        weight=weight
    )
    db.session.add(item)
    db.session.commit()

    if is_ajax():
        return jsonify(success=True, message=f'Item "{name}" added to survey.', item_id=item.id)
    flash(f'Item "{name}" added to survey.', 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/items/<int:item_id>/delete', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_delete_item(survey_id, item_id):
    """Delete an item from the survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    item = SurveyItem.query.get_or_404(item_id)
    if item.survey_id != survey.id:
        abort(404)

    name = item.name
    db.session.delete(item)
    db.session.commit()

    if is_ajax():
        return jsonify(success=True, message=f'Item "{name}" has been removed.')
    flash(f'Item "{name}" has been removed.', 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/items/<int:item_id>/edit', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_edit_item(survey_id, item_id):
    """Edit an item in the survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    item = SurveyItem.query.get_or_404(item_id)
    if item.survey_id != survey.id:
        abort(404)

    # AJAX single-field update
    if is_ajax() and request.form.get('field'):
        field = request.form.get('field')
        value = request.form.get('value', '')
        if field == 'name':
            if not value:
                return jsonify(success=False, message='Item name is required.')
            item.name = value
        elif field == 'capacity':
            try:
                item.capacity = int(value)
            except (ValueError, TypeError):
                return jsonify(success=False, message='Invalid capacity value.')
        elif field == 'weight':
            try:
                item.weight = float(value)
            except (ValueError, TypeError):
                return jsonify(success=False, message='Invalid weight value.')
        elif field == 'description':
            item.description = value
        else:
            return jsonify(success=False, message='Unknown field.')
        db.session.commit()
        return jsonify(success=True, message=f'Item updated.')

    # Check if item has rankings and confirmation wasn't given
    if item.rankings.count() > 0 and not request.form.get('confirm_edit'):
        flash(f'Item "{item.name}" has existing ratings. Please confirm the edit.', 'warning')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    name = request.form.get('name')
    if not name:
        flash('Item name is required.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    item.name = name
    item.description = request.form.get('description', '')
    item.capacity = request.form.get('capacity', 1, type=int)
    item.weight = request.form.get('weight', 1.0, type=float)

    db.session.commit()
    flash(f'Item "{name}" updated successfully.', 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


# ==================== SURVEY PARTICIPANTS ====================

@app.route('/surveys/<int:survey_id>/participants/add', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_add_participant(survey_id):
    """Add an existing user to the survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    user_id = request.form.get('user_id', type=int)
    user = User.query.get_or_404(user_id)

    # Check if already a participant
    existing = SurveyParticipant.query.filter_by(survey_id=survey.id, user_id=user.id).first()
    if existing:
        if is_ajax():
            return jsonify(success=False, message=f'{user.email} is already a participant.')
        flash(f'{user.email} is already a participant.', 'info')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    participant = SurveyParticipant(survey_id=survey.id, user_id=user.id)
    db.session.add(participant)
    db.session.commit()

    if is_ajax():
        return jsonify(success=True, message=f'{user.email} has been added to the survey.')
    flash(f'{user.email} has been added to the survey.', 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/participants/<int:participant_id>/remove', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_remove_participant(survey_id, participant_id):
    """Remove a participant from the survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    participant = SurveyParticipant.query.get_or_404(participant_id)
    if participant.survey_id != survey.id:
        abort(404)

    name = participant.get_display_name()
    db.session.delete(participant)
    db.session.commit()

    if is_ajax():
        return jsonify(success=True, message=f'{name} has been removed from the survey.')
    flash(f'{name} has been removed from the survey.', 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


# ==================== DUMMY USERS ====================

@app.route('/surveys/<int:survey_id>/dummy-users/add', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_add_dummy_users(survey_id):
    """Add dummy users with random preferences to the survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    count = request.form.get('count', 1, type=int)
    if count < 1 or count > 100:
        msg = 'Please enter a number between 1 and 100.'
        if is_ajax():
            return jsonify(success=False, message=msg)
        flash(msg, 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    items = survey.items.all()
    if not items:
        msg = 'Please add items to the survey before adding dummy users.'
        if is_ajax():
            return jsonify(success=False, message=msg)
        flash(msg, 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    # Manual vs random weight/capacity
    weight_random = request.form.get('dummy_weight_random') == 'on'
    capacity_random = request.form.get('dummy_capacity_random') == 'on'
    manual_weight = request.form.get('dummy_weight', type=float)
    manual_capacity = request.form.get('dummy_capacity', type=int)

    # Find the next dummy user number
    existing_dummies = SurveyParticipant.query.filter_by(survey_id=survey.id, is_dummy=True).count()

    for i in range(count):
        dummy_name = f'Dummy User {existing_dummies + i + 1}'
        participant = SurveyParticipant(
            survey_id=survey.id,
            is_dummy=True,
            dummy_name=dummy_name
        )
        if survey.use_weights:
            if weight_random or manual_weight is None:
                participant.user_weight = round(random.uniform(0.5, 5.0), 1)
            else:
                participant.user_weight = manual_weight
        if survey.require_user_capacity:
            if capacity_random or manual_capacity is None:
                participant.user_capacity = random.randint(1, max(1, len(items)))
            else:
                participant.user_capacity = manual_capacity
        db.session.add(participant)
        db.session.flush()  # Get the participant ID

        # Generate random preferences based on ranking mode
        if survey.ranking_mode == 'ordinal':
            # Random ordinal rankings (1 to n)
            ranks = list(range(1, len(items) + 1))
            random.shuffle(ranks)
            for item, rank in zip(items, ranks):
                ranking = ItemRanking(
                    participant_id=participant.id,
                    item_id=item.id,
                    rank=rank
                )
                db.session.add(ranking)
        elif survey.ranking_mode == 'budget':
            # Random point distribution
            remaining = survey.total_points
            points_list = []
            for j in range(len(items) - 1):
                # Assign random portion of remaining points
                pts = random.randint(0, remaining)
                points_list.append(pts)
                remaining -= pts
            points_list.append(remaining)  # Last item gets remaining
            random.shuffle(points_list)
            for item, pts in zip(items, points_list):
                ranking = ItemRanking(
                    participant_id=participant.id,
                    item_id=item.id,
                    points=pts
                )
                db.session.add(ranking)
        else:
            # Rating mode: random score for each item
            for item in items:
                rating = random.randint(survey.min_score, survey.max_score)
                ranking = ItemRanking(
                    participant_id=participant.id,
                    item_id=item.id,
                    rating=rating
                )
                db.session.add(ranking)

    db.session.commit()
    msg = f'{count} dummy user(s) added with random preferences.'
    if is_ajax():
        return jsonify(success=True, message=msg)
    flash(msg, 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/dummy-users/remove-all', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_remove_all_dummy_users(survey_id):
    """Remove all dummy users from the survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    dummies = SurveyParticipant.query.filter_by(survey_id=survey.id, is_dummy=True).all()
    count = len(dummies)
    for dummy in dummies:
        db.session.delete(dummy)
    db.session.commit()

    msg = f'{count} dummy user(s) removed.'
    if is_ajax():
        return jsonify(success=True, message=msg)
    flash(msg, 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/dummy-users/regenerate', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_regenerate_dummy_data(survey_id):
    """Regenerate random ratings for all dummy users."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    items = survey.items.all()
    if not items:
        msg = 'No items in the survey.'
        if is_ajax():
            return jsonify(success=False, message=msg)
        flash(msg, 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    dummies = SurveyParticipant.query.filter_by(survey_id=survey.id, is_dummy=True).all()
    for participant in dummies:
        # Regenerate user weight and capacity if required
        if survey.use_weights:
            participant.user_weight = round(random.uniform(0.5, 5.0), 1)
        if survey.require_user_capacity:
            participant.user_capacity = random.randint(1, max(1, len(items)))

        # Delete existing rankings
        ItemRanking.query.filter_by(participant_id=participant.id).delete()

        # Generate new random preferences
        if survey.ranking_mode == 'ordinal':
            ranks = list(range(1, len(items) + 1))
            random.shuffle(ranks)
            for item, rank in zip(items, ranks):
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id, rank=rank))
        elif survey.ranking_mode == 'budget':
            remaining = survey.total_points
            points_list = []
            for j in range(len(items) - 1):
                pts = random.randint(0, remaining)
                points_list.append(pts)
                remaining -= pts
            points_list.append(remaining)
            random.shuffle(points_list)
            for item, pts in zip(items, points_list):
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id, points=pts))
        else:
            for item in items:
                rating = random.randint(survey.min_score, survey.max_score)
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id, rating=rating))

    db.session.commit()
    msg = f'Regenerated data for {len(dummies)} dummy user(s).'
    if is_ajax():
        return jsonify(success=True, message=msg)
    flash(msg, 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/dummy-users/<int:participant_id>/edit-ratings', methods=['GET', 'POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_edit_dummy_ratings(survey_id, participant_id):
    """Edit ratings for a dummy user."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    participant = SurveyParticipant.query.get_or_404(participant_id)
    if participant.survey_id != survey.id or not participant.is_dummy:
        abort(404)

    if request.method == 'POST':
        # Clear existing rankings
        ItemRanking.query.filter_by(participant_id=participant.id).delete()

        items = survey.items.all()
        if survey.ranking_mode == 'ordinal':
            for item in items:
                rank = request.form.get(f'rank_{item.id}', type=int)
                if rank:
                    db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id, rank=rank))
        elif survey.ranking_mode in ('budget', 'points'):
            for item in items:
                points = request.form.get(f'points_{item.id}', 0, type=int)
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id, points=points))
        else:
            for item in items:
                rating = request.form.get(f'rating_{item.id}', type=int)
                if rating is not None:
                    db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id, rating=rating))

        db.session.commit()
        flash(f'Ratings for {participant.dummy_name} updated.', 'success')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    # GET: show current ratings
    rankings = {r.item_id: r for r in participant.rankings.all()}
    return render_template('survey/edit_dummy_ratings.html', survey=survey, participant=participant, rankings=rankings)


# ==================== AJAX INLINE EDITING ====================

@app.route('/surveys/<int:survey_id>/ratings/update', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_update_rating(survey_id):
    """Update a single rating value (AJAX only)."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    participant_id = request.form.get('participant_id', type=int)
    item_id = request.form.get('item_id', type=int)
    value = request.form.get('value', '')

    participant = SurveyParticipant.query.get_or_404(participant_id)
    if participant.survey_id != survey.id or not participant.is_dummy:
        return jsonify(success=False, message='Can only edit dummy user ratings.')

    item = SurveyItem.query.get_or_404(item_id)
    if item.survey_id != survey.id:
        return jsonify(success=False, message='Item not in this survey.')

    # Find or create the ranking
    ranking = ItemRanking.query.filter_by(participant_id=participant_id, item_id=item_id).first()
    if not ranking:
        ranking = ItemRanking(participant_id=participant_id, item_id=item_id)
        db.session.add(ranking)

    try:
        val = int(value) if value else 0
    except (ValueError, TypeError):
        return jsonify(success=False, message='Invalid value.')

    if survey.ranking_mode == 'ordinal':
        ranking.rank = val
    elif survey.ranking_mode in ('budget', 'points'):
        ranking.points = val
    else:
        ranking.rating = val

    db.session.commit()
    return jsonify(success=True)


@app.route('/surveys/<int:survey_id>/participants/<int:participant_id>/update-field', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_update_participant_field(survey_id, participant_id):
    """Update a single field on a participant (AJAX only)."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    participant = SurveyParticipant.query.get_or_404(participant_id)
    if participant.survey_id != survey.id:
        abort(404)

    field = request.form.get('field')
    value = request.form.get('value', '')

    if field == 'user_weight':
        try:
            participant.user_weight = float(value) if value else None
        except (ValueError, TypeError):
            return jsonify(success=False, message='Invalid weight value.')
    elif field == 'user_capacity':
        try:
            participant.user_capacity = int(value) if value else None
        except (ValueError, TypeError):
            return jsonify(success=False, message='Invalid capacity value.')
    else:
        return jsonify(success=False, message='Unknown field.')

    db.session.commit()
    return jsonify(success=True)


# ==================== ALGORITHM EXECUTION ====================

def survey_to_fairpyx_valuations(survey):
    """Convert survey data to fairpyx-compatible valuations format."""
    participants = survey.participants.all()
    items = survey.items.all()

    valuations = {}
    for p in participants:
        name = p.get_display_name()
        valuations[name] = {}
        for ranking in p.rankings.all():
            item_name = ranking.item.name
            if survey.ranking_mode == 'ordinal':
                # Convert rank to value (higher rank = higher value)
                value = len(items) - ranking.rank + 1
            elif survey.ranking_mode in ('budget', 'points'):
                value = ranking.points
            else:  # rating
                value = ranking.rating
            valuations[name][item_name] = value

    return valuations


def get_item_capacities(survey):
    """Get item capacities as a dict. If use_item_capacity is disabled, return large capacity for all."""
    if survey.use_item_capacity:
        return {item.name: item.capacity for item in survey.items.all()}
    # When item capacity is disabled, use number of participants as capacity (effectively unlimited)
    num_participants = survey.participants.count()
    return {item.name: max(num_participants, 1) for item in survey.items.all()}


@app.route('/surveys/<int:survey_id>/run-algorithm', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_run_algorithm(survey_id):
    """Run a fairpyx algorithm on the survey data."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    algorithm = request.form.get('algorithm')
    if not algorithm:
        flash('Please select an algorithm.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    # Check if there are participants with rankings
    participants_with_rankings = [p for p in survey.participants.all() if p.rankings.count() > 0]
    if not participants_with_rankings:
        flash('No participants have submitted rankings yet.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    items = survey.items.all()
    if not items:
        flash('No items in the survey.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    try:
        from fairpyx import Instance, AllocationBuilder, divide

        valuations = survey_to_fairpyx_valuations(survey)
        item_capacities = get_item_capacities(survey)

        # Create fairpyx instance
        instance = Instance(valuations=valuations, item_capacities=item_capacities)

        # Run the selected algorithm
        if algorithm == 'round_robin':
            from fairpyx.algorithms.picking_sequence import round_robin
            allocation = divide(round_robin, instance)
        elif algorithm == 'bidirectional_round_robin':
            from fairpyx.algorithms.picking_sequence import bidirectional_round_robin
            allocation = divide(bidirectional_round_robin, instance)
        elif algorithm == 'iterated_maximum_matching':
            from fairpyx.algorithms.iterated_maximum_matching import iterated_maximum_matching
            allocation = divide(iterated_maximum_matching, instance)
        elif algorithm == 'utilitarian_matching':
            from fairpyx.algorithms.utilitarian_matching import utilitarian_matching
            allocation = divide(utilitarian_matching, instance)
        elif algorithm == 'serial_dictatorship':
            from fairpyx.algorithms.picking_sequence import serial_dictatorship
            allocation = divide(serial_dictatorship, instance)
        else:
            flash(f'Unknown algorithm: {algorithm}', 'danger')
            return redirect(url_for('survey_edit', survey_id=survey.id))

        # Convert allocation to JSON-serializable format
        result_data = {
            'allocation': dict(allocation),
            'algorithm': algorithm,
            'participants': [p.get_display_name() for p in participants_with_rankings],
            'items': [item.name for item in items]
        }

        # Save result
        result = AllocationResult(
            survey_id=survey.id,
            algorithm=algorithm,
            result_json=json.dumps(result_data)
        )
        db.session.add(result)
        db.session.commit()

        flash(f'Algorithm "{algorithm}" completed successfully!', 'success')
        return redirect(url_for('survey_results', survey_id=survey.id))

    except Exception as e:
        flash(f'Error running algorithm: {str(e)}', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/results')
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_results(survey_id):
    """View allocation results for a survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    results = survey.allocation_results.order_by(AllocationResult.created_at.desc()).all()
    # Parse JSON for each result
    parsed_results = []
    for r in results:
        parsed_results.append({
            'id': r.id,
            'algorithm': r.algorithm,
            'created_at': r.created_at,
            'data': json.loads(r.result_json) if r.result_json else {}
        })

    return render_template('survey/results.html', survey=survey, results=parsed_results)


@app.route('/surveys/<int:survey_id>/results/<int:result_id>/delete', methods=['POST'])
@auth_required()
@roles_accepted('admin', 'moderator')
def survey_delete_result(survey_id, result_id):
    """Delete an allocation result."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    result = AllocationResult.query.get_or_404(result_id)
    if result.survey_id != survey.id:
        abort(404)

    db.session.delete(result)
    db.session.commit()

    flash('Result deleted.', 'success')
    return redirect(url_for('survey_results', survey_id=survey.id))


# ==================== INVITE LINK ====================

@app.route('/join/<invite_code>')
def survey_join(invite_code):
    """Join a survey via invite link."""
    survey = Survey.query.filter_by(invite_code=invite_code).first_or_404()

    if not current_user.is_authenticated:
        # Redirect to login with next parameter to return here after auth
        flash('Please log in or register to join this survey.', 'info')
        return redirect(url_for('security.login', next=request.url))

    # Check if already a participant
    existing = SurveyParticipant.query.filter_by(survey_id=survey.id, user_id=current_user.id).first()
    if existing:
        flash('You are already a participant in this survey.', 'info')
        return redirect(url_for('survey_rank', survey_id=survey.id))

    # Add as participant
    participant = SurveyParticipant(survey_id=survey.id, user_id=current_user.id)
    db.session.add(participant)
    db.session.commit()

    flash(f'You have joined the survey "{survey.title}"!', 'success')
    return redirect(url_for('survey_rank', survey_id=survey.id))


# ==================== RANKING INTERFACE ====================

@app.route('/surveys/<int:survey_id>/rank', methods=['GET', 'POST'])
@auth_required()
def survey_rank(survey_id):
    """Participant ranking interface."""
    survey = Survey.query.get_or_404(survey_id)

    # Check if user is a participant
    participant = SurveyParticipant.query.filter_by(survey_id=survey.id, user_id=current_user.id).first()
    if not participant:
        flash('You are not a participant in this survey.', 'danger')
        return redirect(url_for('my_surveys'))

    if request.method == 'POST':
        if not survey.is_open:
            flash('This survey is closed for changes.', 'danger')
            return redirect(url_for('survey_rank', survey_id=survey.id))

        # Process user weight if weights are enabled
        if survey.use_weights:
            user_weight = request.form.get('user_weight', type=float)
            if user_weight is None or user_weight <= 0:
                flash('Please enter a valid weight.', 'danger')
                return redirect(url_for('survey_rank', survey_id=survey.id))
            participant.user_weight = user_weight

        if survey.require_user_capacity:
            user_capacity = request.form.get('user_capacity', type=int)
            if user_capacity is None or user_capacity <= 0:
                flash('Please enter a valid capacity.', 'danger')
                return redirect(url_for('survey_rank', survey_id=survey.id))
            participant.user_capacity = user_capacity

        # Clear existing rankings
        ItemRanking.query.filter_by(participant_id=participant.id).delete()

        items = survey.items.all()

        if survey.ranking_mode == 'ordinal':
            # Process ordinal rankings
            for item in items:
                rank = request.form.get(f'rank_{item.id}', type=int)
                if rank:
                    ranking = ItemRanking(
                        participant_id=participant.id,
                        item_id=item.id,
                        rank=rank
                    )
                    db.session.add(ranking)
        elif survey.ranking_mode in ('budget', 'points'):
            # Process budget-based rankings (distribute total points)
            total_assigned = 0
            for item in items:
                points = request.form.get(f'points_{item.id}', 0, type=int)
                total_assigned += points
                ranking = ItemRanking(
                    participant_id=participant.id,
                    item_id=item.id,
                    points=points
                )
                db.session.add(ranking)

            if total_assigned != survey.total_points:
                db.session.rollback()
                flash(f'Total points must equal {survey.total_points}. You assigned {total_assigned}.', 'danger')
                return redirect(url_for('survey_rank', survey_id=survey.id))
        else:
            # Process rating mode (score each item independently)
            for item in items:
                rating = request.form.get(f'rating_{item.id}', type=int)
                if rating is not None:
                    if rating < survey.min_score or rating > survey.max_score:
                        db.session.rollback()
                        flash(f'Rating must be between {survey.min_score} and {survey.max_score}.', 'danger')
                        return redirect(url_for('survey_rank', survey_id=survey.id))
                    ranking = ItemRanking(
                        participant_id=participant.id,
                        item_id=item.id,
                        rating=rating
                    )
                    db.session.add(ranking)

        db.session.commit()
        flash('Your rankings have been saved!', 'success')
        return redirect(url_for('survey_rank', survey_id=survey.id))

    # Get current rankings
    rankings = {r.item_id: r for r in participant.rankings.all()}

    return render_template('survey/rank.html', survey=survey, participant=participant, rankings=rankings)


@app.route('/surveys/<int:survey_id>/my-results')
@auth_required()
def survey_my_results(survey_id):
    """View the current user's allocation results for a survey."""
    survey = Survey.query.get_or_404(survey_id)

    participant = SurveyParticipant.query.filter_by(survey_id=survey.id, user_id=current_user.id).first()
    if not participant:
        flash('You are not a participant in this survey.', 'danger')
        return redirect(url_for('my_surveys'))

    display_name = participant.get_display_name()
    results = survey.allocation_results.order_by(AllocationResult.created_at.desc()).all()

    # Build user-specific results with explanation
    user_results = []
    rankings = {r.item_id: r for r in participant.rankings.all()}
    items_by_name = {item.name: item for item in survey.items.all()}

    for r in results:
        data = json.loads(r.result_json) if r.result_json else {}
        allocation = data.get('allocation', {})
        my_items = allocation.get(display_name, [])

        # Compute explanation for each allocated item
        explained_items = []
        total_value = 0
        for item_name in my_items:
            item = items_by_name.get(item_name)
            if item and item.id in rankings:
                ranking = rankings[item.id]
                if survey.ranking_mode == 'ordinal':
                    value = ranking.rank
                    label = f'Rank {value}'
                elif survey.ranking_mode in ('budget', 'points'):
                    value = ranking.points
                    label = f'{value} points'
                else:
                    value = ranking.rating
                    label = f'Rating {value}'
                total_value += value
                explained_items.append({'name': item_name, 'value': value, 'label': label})
            else:
                explained_items.append({'name': item_name, 'value': 0, 'label': 'N/A'})

        user_results.append({
            'algorithm': r.algorithm,
            'created_at': r.created_at,
            'items': explained_items,
            'total_value': total_value
        })

    return render_template('survey/my_results.html', survey=survey, participant=participant, user_results=user_results)


@app.route('/my-surveys')
@auth_required()
def my_surveys():
    """List surveys the current user is participating in."""
    participations = SurveyParticipant.query.filter_by(user_id=current_user.id).all()
    return render_template('survey/my_surveys.html', participations=participations)


with app.app_context():
    db.create_all()
    create_roles()
    create_admin_user()

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5032)
