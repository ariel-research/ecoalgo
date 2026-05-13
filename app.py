from flask import Flask, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_security import Security, SQLAlchemyUserDatastore, current_user, auth_required, roles_required, hash_password, user_registered
from flask_wtf.csrf import CSRFProtect
from config import Config
from models import db, User, Role, Survey, SurveyItem, SurveyParticipant, ItemRanking, AllocationResult, ItemConflict
from forms import ExtendedRegisterForm
from algorithms import ALGORITHMS, CATEGORIES, get_algo_data_for_template
import uuid
import json
import random
import secrets
import concurrent.futures


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
    """Auto-generate username and grant moderator role on registration."""
    if not user.username:
        base = user.email.split('@')[0]
        username = base
        counter = 1
        while User.query.filter_by(username=username).first():
            username = f'{base}{counter}'
            counter += 1
        user.username = username
    mod_role = user_datastore.find_role('moderator')
    if mod_role and mod_role not in user.roles:
        user_datastore.add_role_to_user(user, mod_role)
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


def is_valid_weight(w):
    """Weight must be a positive multiple of 0.5 (e.g. 0.5, 1.0, 1.5, 2.0...)."""
    return w > 0 and (w * 2) % 1 == 0


# Flags automatically applied when a survey category is chosen.
CATEGORY_SETTINGS = {
    'fair_division': {
        'use_weights':            False,
        'require_user_capacity':  False,
        'use_item_capacity':      True,  # each item goes to exactly 1 agent
    },
    'capacitated_allocation': {
        'use_weights':            True,
        'require_user_capacity':  True,
        'use_item_capacity':      True,
    },
    'approval_voting': {
        'use_weights':            False,
        'require_user_capacity':  False,
        'use_item_capacity':      False,
        'ranking_mode':           'approval', # forced — participants tick approved items
    },
    'participatory_budgeting': {
        'use_weights':            False,  # item.weight used as project cost, not agent weight
        'require_user_capacity':  False,
        'use_item_capacity':      False,
        'ranking_mode':           'approval', # participants tick projects they support
        # survey.total_points = global project budget; item.weight = project cost
    },
}


def _apply_category_settings(survey, category, ranking_mode_form_value):
    """Set survey flags based on the chosen category."""
    settings = CATEGORY_SETTINGS[category]
    survey.category = category
    survey.use_weights = settings['use_weights']
    survey.require_user_capacity = settings['require_user_capacity']
    survey.use_item_capacity = settings['use_item_capacity']
    survey.ranking_mode = settings.get('ranking_mode') or ranking_mode_form_value or 'ordinal'


def _known_users_for(creator_id):
    """Real users who have joined at least one survey created by creator_id."""
    survey_ids = [s.id for s in Survey.query.filter_by(creator_id=creator_id)
                  .with_entities(Survey.id).all()]
    if not survey_ids:
        return []
    uid_rows = (db.session.query(SurveyParticipant.user_id)
                .filter(SurveyParticipant.survey_id.in_(survey_ids))
                .filter(SurveyParticipant.user_id.isnot(None))
                .filter(SurveyParticipant.is_dummy == False)
                .distinct().all())
    ids = [r[0] for r in uid_rows]
    if not ids:
        return []
    return (User.query
            .filter(User.id.in_(ids))
            .filter(User.is_system_dummy == False)
            .filter(User.id != creator_id)
            .all())


def _algo_list_grouped(category):
    """Return algorithms for a category, grouped for template rendering."""
    groups = {}
    order = []
    for name, entry in ALGORITHMS.items():
        if entry['category'] != category:
            continue
        g = entry.get('group', 'Other')
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append({'value': name, 'label': entry['display_name'],
                          'description': entry.get('description', '')})
    return [{'group': g, 'algorithms': groups[g]} for g in order]


CATEGORY_INFO = {
    'fair_division': {
        'icon':        '⚖️',
        'description': 'Assign items to agents based on ordinal or cardinal utilities. Algorithms guarantee various fairness and efficiency conditions.',
    },
    'capacitated_allocation': {
        'icon':        '🎓',
        'description': 'Item allocation where both agents and items may have capacity limits and weights — suited for course seat assignment problems.',
    },
    'approval_voting': {
        'icon':        '✅',
        'description': 'Agents approve a subset of items; algorithms elect a winning committee using proportional or load-balancing rules.',
    },
    'participatory_budgeting': {
        'icon':        '🏛️',
        'description': 'Agents approve subsets of projects with costs; algorithms decide which projects to fund within a fixed community budget.',
    },
}


@app.route('/')
def home():
    categories = [
        {'key': k, 'label': CATEGORIES[k], **CATEGORY_INFO.get(k, {})}
        for k in CATEGORIES
    ]
    return render_template('home.html', categories=categories)


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


@app.route('/admin/settings', methods=['GET', 'POST'])
@auth_required()
@roles_required('admin')
def admin_settings():
    """Admin settings page."""
    if request.method == 'POST':
        timeout_val = request.form.get('algorithm_timeout', '')
        try:
            timeout = int(timeout_val)
            if timeout < 1:
                raise ValueError
            app.config['ALGORITHM_TIMEOUT'] = timeout
            flash(f'Algorithm timeout updated to {timeout} seconds.', 'success')
        except ValueError:
            flash('Timeout must be a positive integer.', 'danger')
        return redirect(url_for('admin_settings'))
    return render_template('admin/settings.html', timeout=app.config['ALGORITHM_TIMEOUT'])


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
def moderator_panel():
    """Moderator panel - accessible by admins and moderators."""
    surveys = Survey.query.filter_by(creator_id=current_user.id).all()
    return render_template('moderator/panel.html', surveys=surveys)


# ==================== SURVEY ROUTES ====================

@app.route('/surveys')
@auth_required()
def survey_list():
    """List all surveys created by the current moderator."""
    surveys = Survey.query.filter_by(creator_id=current_user.id).all()
    return render_template('survey/list.html', surveys=surveys)


@app.route('/surveys/create', methods=['GET', 'POST'])
@auth_required()
def survey_create():
    """Create a new survey."""
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        category = request.form.get('category', '').strip()
        ranking_mode = request.form.get('ranking_mode', 'ordinal')
        total_points = request.form.get('total_points', 100, type=int)
        min_score = request.form.get('min_score', 1, type=int)
        max_score = request.form.get('max_score', 10, type=int)

        if not title:
            flash('Survey title is required.', 'danger')
            return render_template('survey/create.html', categories=CATEGORIES)
        if category not in CATEGORY_SETTINGS:
            flash('Please select a valid category.', 'danger')
            return render_template('survey/create.html', categories=CATEGORIES)

        survey = Survey(
            title=title,
            description=description,
            creator_id=current_user.id,
            total_points=total_points,
            min_score=min_score,
            max_score=max_score,
        )
        _apply_category_settings(survey, category, ranking_mode)
        db.session.add(survey)
        db.session.commit()

        flash(f'Survey "{title}" created successfully!', 'success')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    return render_template('survey/create.html', categories=CATEGORIES)


@app.route('/surveys/<int:survey_id>')
@auth_required()
def survey_edit(survey_id):
    """Edit survey details, manage items and participants."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    if current_user.has_role('admin'):
        known_users = (User.query
                       .filter(User.is_system_dummy == False)
                       .filter(User.id != current_user.id)
                       .all())
    else:
        known_users = _known_users_for(current_user.id)

    # Build rating matrix for the rating table (includes dummies for management)
    items = survey.items.all()
    participants = survey.participants.all()
    rating_matrix = []
    for p in participants:
        row = {'participant': p, 'ratings': {}}
        for r in p.rankings.all():
            if survey.ranking_mode == 'ordinal':
                row['ratings'][r.item_id] = r.rank
            elif survey.ranking_mode in ('budget', 'points', 'approval'):
                row['ratings'][r.item_id] = r.points
            else:
                row['ratings'][r.item_id] = r.rating
        rating_matrix.append(row)

    dummy_count = survey.participants.filter_by(is_dummy=True).count()
    conflicts = survey.conflicts.all()
    grouped_algos = _algo_list_grouped(survey.category) if survey.category else []
    return render_template(
        'survey/edit.html',
        survey=survey,
        known_users=known_users,
        items=items,
        rating_matrix=rating_matrix,
        dummy_count=dummy_count,
        conflicts=conflicts,
        categories=CATEGORIES,
        grouped_algos=grouped_algos,
    )


@app.route('/surveys/<int:survey_id>/update', methods=['POST'])
@auth_required()
def survey_update(survey_id):
    """Update survey settings."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    survey.title = request.form.get('title', survey.title)
    survey.description = request.form.get('description', survey.description)
    survey.total_points = request.form.get('total_points', survey.total_points, type=int)
    survey.min_score = request.form.get('min_score', survey.min_score, type=int)
    survey.max_score = request.form.get('max_score', survey.max_score, type=int)

    new_category = request.form.get('category', survey.category)
    if new_category in CATEGORY_SETTINGS:
        _apply_category_settings(survey, new_category, request.form.get('ranking_mode', survey.ranking_mode))
    else:
        survey.ranking_mode = request.form.get('ranking_mode', survey.ranking_mode)

    db.session.commit()
    flash('Survey updated successfully!', 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/toggle', methods=['POST'])
@auth_required()
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


@app.route('/surveys/<int:survey_id>/items/import-csv', methods=['POST'])
@auth_required()
def survey_import_items_csv(survey_id):
    """Import items from a CSV file."""
    import csv, io
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    f = request.files.get('csv_file')
    if not f or not f.filename:
        return jsonify(success=False, message='No file selected.')
    if not f.filename.lower().endswith('.csv'):
        return jsonify(success=False, message='File must be a .csv file.')

    # Build expected column order based on survey settings
    # name, [capacity], [weight], [description]
    try:
        text = f.read().decode('utf-8-sig')  # utf-8-sig strips BOM if present
    except UnicodeDecodeError:
        return jsonify(success=False, message='File must be UTF-8 encoded.')

    reader = csv.reader(io.StringIO(text))
    added = 0
    errors = []
    for i, row in enumerate(reader, start=1):
        if not row or all(cell.strip() == '' for cell in row):
            continue  # skip blank rows
        col = 0
        name = row[col].strip() if col < len(row) else ''
        col += 1
        if not name:
            errors.append(f'Row {i}: missing item name.')
            continue

        capacity = 1
        if survey.use_item_capacity:
            raw = row[col].strip() if col < len(row) else ''
            col += 1
            try:
                capacity = int(raw) if raw else 1
            except ValueError:
                errors.append(f'Row {i}: invalid capacity "{raw}".')
                continue

        weight = 1.0
        if survey.use_weights:
            raw = row[col].strip() if col < len(row) else ''
            col += 1
            try:
                weight = float(raw) if raw else 1.0
            except ValueError:
                errors.append(f'Row {i}: invalid weight "{raw}".')
                continue

        description = row[col].strip() if col < len(row) else ''

        db.session.add(SurveyItem(
            survey_id=survey.id,
            name=name,
            description=description or None,
            capacity=capacity,
            weight=weight
        ))
        added += 1

    if added == 0 and errors:
        db.session.rollback()
        return jsonify(success=False, message='No items imported. Errors: ' + '; '.join(errors))

    db.session.commit()
    msg = f'{added} item(s) imported.'
    if errors:
        msg += ' Skipped rows: ' + '; '.join(errors)
    return jsonify(success=True, message=msg)


@app.route('/surveys/<int:survey_id>/items/<int:item_id>/delete', methods=['POST'])
@auth_required()
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


# ==================== ITEM CONFLICTS ====================

@app.route('/surveys/<int:survey_id>/conflicts/add', methods=['POST'])
@auth_required()
def survey_add_conflict(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    id1 = request.form.get('item1_id', type=int)
    id2 = request.form.get('item2_id', type=int)

    if not id1 or not id2 or id1 == id2:
        return jsonify(success=False, message='Please select two different items.')

    # Normalise order
    lo, hi = (id1, id2) if id1 < id2 else (id2, id1)

    # Verify both items belong to this survey
    items = {i.id for i in survey.items.all()}
    if lo not in items or hi not in items:
        return jsonify(success=False, message='One or both items not found in this survey.')

    if ItemConflict.query.filter_by(survey_id=survey.id, item1_id=lo, item2_id=hi).first():
        return jsonify(success=False, message='This conflict already exists.')

    db.session.add(ItemConflict(survey_id=survey.id, item1_id=lo, item2_id=hi))
    db.session.commit()
    return jsonify(success=True, message='Conflict added.')


@app.route('/surveys/<int:survey_id>/conflicts/<int:conflict_id>/delete', methods=['POST'])
@auth_required()
def survey_delete_conflict(survey_id, conflict_id):
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    conflict = ItemConflict.query.get_or_404(conflict_id)
    if conflict.survey_id != survey.id:
        abort(404)

    db.session.delete(conflict)
    db.session.commit()
    return jsonify(success=True, message='Conflict removed.')


@app.route('/surveys/<int:survey_id>/conflicts/import-csv', methods=['POST'])
@auth_required()
def survey_import_conflicts_csv(survey_id):
    import csv, io
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    f = request.files.get('csv_file')
    if not f or not f.filename:
        return jsonify(success=False, message='No file selected.')
    if not f.filename.lower().endswith('.csv'):
        return jsonify(success=False, message='File must be a .csv file.')

    try:
        text = f.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return jsonify(success=False, message='File must be UTF-8 encoded.')

    items_by_name = {i.name.strip().lower(): i for i in survey.items.all()}
    existing = {(c.item1_id, c.item2_id) for c in survey.conflicts.all()}

    added, errors = 0, []
    reader = csv.reader(io.StringIO(text))
    for i, row in enumerate(reader, start=1):
        row = [c.strip() for c in row]
        if not any(row):
            continue
        if len(row) < 2:
            errors.append(f'Row {i}: need two item names.')
            continue

        name1, name2 = row[0].lower(), row[1].lower()
        if name1 == name2:
            errors.append(f'Row {i}: both names are the same ("{row[0]}").')
            continue

        item1 = items_by_name.get(name1)
        item2 = items_by_name.get(name2)
        if not item1:
            errors.append(f'Row {i}: item "{row[0]}" not found.')
            continue
        if not item2:
            errors.append(f'Row {i}: item "{row[1]}" not found.')
            continue

        lo, hi = (item1.id, item2.id) if item1.id < item2.id else (item2.id, item1.id)
        if (lo, hi) in existing:
            errors.append(f'Row {i}: conflict ({row[0]}, {row[1]}) already exists.')
            continue

        db.session.add(ItemConflict(survey_id=survey.id, item1_id=lo, item2_id=hi))
        existing.add((lo, hi))
        added += 1

    if added == 0 and errors:
        db.session.rollback()
        return jsonify(success=False, message='No conflicts imported. ' + '; '.join(errors))

    db.session.commit()
    msg = f'{added} conflict(s) imported.'
    if errors:
        msg += ' Skipped: ' + '; '.join(errors)
    return jsonify(success=True, message=msg)


@app.route('/surveys/<int:survey_id>/items/<int:item_id>/edit', methods=['POST'])
@auth_required()
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
def survey_add_participant(survey_id):
    """Add an existing user to the survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    user_id = request.form.get('user_id', type=int)
    user = User.query.get_or_404(user_id)

    # Only allow adding users who have already joined one of the creator's surveys
    if not current_user.has_role('admin'):
        known_ids = {u.id for u in _known_users_for(current_user.id)}
        if user.id not in known_ids:
            msg = 'You can only add users who have already joined one of your surveys.'
            if is_ajax():
                return jsonify(success=False, message=msg)
            flash(msg, 'danger')
            return redirect(url_for('survey_edit', survey_id=survey.id))

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
def survey_add_dummy_users(survey_id):
    """Add system dummy users with random preferences to the survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    count = request.form.get('count', 1, type=int)
    if count < 1 or count > 1000:
        msg = 'Please enter a number between 1 and 1000.'
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

    # Pick system dummies not already assigned to this survey
    already_ids = {p.user_id for p in
                   SurveyParticipant.query.filter_by(survey_id=survey.id, is_dummy=True)
                   .filter(SurveyParticipant.user_id.isnot(None)).all()}
    query = User.query.filter_by(is_system_dummy=True)
    if already_ids:
        query = query.filter(User.id.notin_(already_ids))
    available = query.limit(count).all()

    if not available:
        msg = 'All system dummy users are already in this survey.'
        if is_ajax():
            return jsonify(success=False, message=msg)
        flash(msg, 'warning')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    actual_count = len(available)
    existing_count = SurveyParticipant.query.filter_by(survey_id=survey.id, is_dummy=True).count()

    weight_random = request.form.get('dummy_weight_random') == 'on'
    capacity_random = request.form.get('dummy_capacity_random') == 'on'
    manual_weight = request.form.get('dummy_weight', type=float)
    manual_capacity = request.form.get('dummy_capacity', type=int)

    if survey.use_weights and manual_weight is not None and not is_valid_weight(manual_weight):
        return jsonify(success=False, message='Weight must be a whole number or half (e.g. 1, 1.5, 2, 2.5).')

    for i, dummy_user in enumerate(available):
        participant = SurveyParticipant(
            survey_id=survey.id,
            user_id=dummy_user.id,
            is_dummy=True,
            dummy_name=f'Dummy {existing_count + i + 1}',
        )
        if survey.use_weights:
            participant.user_weight = (
                random.choice([0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0])
                if weight_random or manual_weight is None else manual_weight
            )
        if survey.require_user_capacity:
            participant.user_capacity = (
                random.randint(1, max(1, len(items)))
                if capacity_random or manual_capacity is None else manual_capacity
            )
        db.session.add(participant)
        db.session.flush()

        if survey.ranking_mode == 'ordinal':
            ranks = list(range(1, len(items) + 1))
            random.shuffle(ranks)
            for item, rank in zip(items, ranks):
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id, rank=rank))
        elif survey.ranking_mode == 'budget':
            remaining = survey.total_points
            pts_list = []
            for _ in range(len(items) - 1):
                pts = random.randint(0, remaining)
                pts_list.append(pts)
                remaining -= pts
            pts_list.append(remaining)
            random.shuffle(pts_list)
            for item, pts in zip(items, pts_list):
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id, points=pts))
        elif survey.ranking_mode == 'approval':
            for item in items:
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id,
                                           points=random.randint(0, 1)))
        else:
            for item in items:
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id,
                                           rating=random.randint(survey.min_score, survey.max_score)))

    db.session.commit()
    msg = f'{actual_count} dummy user(s) added with random preferences.'
    if actual_count < count:
        msg += f' (Only {actual_count} system dummies were available.)'
    if is_ajax():
        return jsonify(success=True, message=msg)
    flash(msg, 'success')
    return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/dummy-users/remove-all', methods=['POST'])
@auth_required()
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
            participant.user_weight = random.choice([0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0])
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
        elif survey.ranking_mode == 'approval':
            for item in items:
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id,
                                           points=random.randint(0, 1)))
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
            total_assigned = 0
            rows = []
            for item in items:
                points = request.form.get(f'points_{item.id}', 0, type=int)
                total_assigned += points
                rows.append(ItemRanking(participant_id=participant.id, item_id=item.id, points=points))
            if total_assigned != survey.total_points:
                db.session.rollback()
                flash(f'Total points must equal {survey.total_points}. You assigned {total_assigned}.', 'danger')
                return redirect(request.url)
            for row in rows:
                db.session.add(row)
        elif survey.ranking_mode == 'approval':
            for item in items:
                approved = request.form.get(f'approved_{item.id}') == 'on'
                db.session.add(ItemRanking(participant_id=participant.id, item_id=item.id,
                                           points=1 if approved else 0))
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
    elif survey.ranking_mode in ('budget', 'points', 'approval'):
        ranking.points = val
    else:
        ranking.rating = val

    db.session.commit()
    return jsonify(success=True)


@app.route('/surveys/<int:survey_id>/participants/<int:participant_id>/update-field', methods=['POST'])
@auth_required()
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
            w = float(value) if value else None
            if w is not None and not is_valid_weight(w):
                return jsonify(success=False, message='Weight must be a whole number or half (e.g. 1, 1.5, 2, 2.5).')
            participant.user_weight = w
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

@app.route('/surveys/<int:survey_id>/run-algorithm', methods=['POST'])
@auth_required()
def survey_run_algorithm(survey_id):
    """Run an algorithm on the survey data."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    algorithm = request.form.get('algorithm', '').strip()
    category = survey.category

    if not category:
        flash('This survey has no category set. Update the survey settings first.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    if not algorithm:
        flash('Please select an algorithm.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    entry = ALGORITHMS.get(algorithm)
    if not entry or entry['category'] != category:
        flash('Invalid algorithm for this survey\'s category.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    participants_with_rankings = [p for p in survey.participants.all() if p.rankings.count() > 0]
    if not participants_with_rankings:
        flash('No participants have submitted rankings yet.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    if not survey.items.count():
        flash('No items in the survey.', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))

    try:
        if entry.get('runner') == 'pabutools':
            import importlib
            mod = importlib.import_module(entry['module'])
            algo_func = getattr(mod, entry['function'])
            instance, profile = entry['builder'](survey)

            kwargs = {}
            if entry.get('needs_sat_class'):
                from pabutools.election.satisfaction import Cardinality_Sat
                kwargs['sat_class'] = Cardinality_Sat

            funded = algo_func(instance, profile, **kwargs)

            items = survey.items.all()
            result_data = {
                'funded_projects': sorted(str(p) for p in funded),
                'total_cost':      float(sum(p.cost for p in funded)),
                'budget':          float(instance.budget_limit),
                'algorithm':       algorithm,
                'items':           [item.name for item in items],
                'participants':    [p.get_display_name() for p in survey.participants.all()
                                    if p.rankings.count() > 0],
            }
            db.session.add(AllocationResult(
                survey_id=survey.id,
                category=category,
                algorithm=algorithm,
                result_json=json.dumps(result_data),
                logs=None,
            ))
            db.session.commit()
            flash(f'"{entry["display_name"]}" completed successfully!', 'success')
            return redirect(url_for('survey_results', survey_id=survey.id))

        if entry.get('runner') == 'abcvoting':
            from abcvoting import abcrules
            n_items = survey.items.count()
            committeesize = request.form.get('committeesize', type=int)
            if not committeesize or committeesize < 1:
                committeesize = max(1, n_items // 2)
            committeesize = min(committeesize, n_items)

            profile = entry['builder'](survey)
            extra_kwargs = {}
            if 'completion' in entry.get('extra_params', []):
                completion = request.form.get('completion', 'seqphragmen')
                extra_kwargs['completion'] = None if completion == 'none' else completion
            committees = abcrules.compute(entry['rule_id'], profile, committeesize=committeesize,
                                          **extra_kwargs)

            items = survey.items.all()
            result_data = {
                'committees':    [[profile.cand_names[c] for c in comm] for comm in committees],
                'committeesize': committeesize,
                'algorithm':     algorithm,
                'items':         [item.name for item in items],
                'participants':  [p.get_display_name() for p in survey.participants.all()
                                  if p.rankings.count() > 0],
            }
            db.session.add(AllocationResult(
                survey_id=survey.id,
                category=category,
                algorithm=algorithm,
                result_json=json.dumps(result_data),
                logs=None,
            ))
            db.session.commit()
            flash(f'"{entry["display_name"]}" completed successfully!', 'success')
            return redirect(url_for('survey_results', survey_id=survey.id))

        import importlib, io, logging, inspect
        from fairpyx import divide
        from fairpyx.explanations import SingleExplanationLogger

        instance = entry['builder'](survey)

        mod = importlib.import_module(entry['module'])
        algo_func = getattr(mod, entry['function'])

        log_stream = io.StringIO()
        log_handler = logging.StreamHandler(log_stream)
        log_handler.setLevel(logging.DEBUG)
        log_handler.setFormatter(logging.Formatter('%(message)s'))

        fairpyx_root = logging.getLogger('fairpyx')
        prev_level = fairpyx_root.level
        fairpyx_root.setLevel(logging.DEBUG)
        fairpyx_root.addHandler(log_handler)

        expl_logger = logging.getLogger(f'fairpyx.explanation.{uuid.uuid4().hex}')
        expl_logger.setLevel(logging.DEBUG)
        expl_logger.propagate = False
        expl_logger.addHandler(log_handler)
        explanation_logger = SingleExplanationLogger(expl_logger)

        timeout_seconds = app.config.get('ALGORITHM_TIMEOUT', 60)

        def run_algo():
            algo_params = inspect.signature(algo_func).parameters
            if 'explanation_logger' in algo_params:
                return divide(algo_func, instance, explanation_logger=explanation_logger)
            else:
                explanation_logger.explain_valuations(instance)
                result = divide(algo_func, instance)
                explanation_logger.explain_allocation(result, instance)
                return result

        timed_out = False
        allocation = None
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(run_algo)
        try:
            allocation = future.result(timeout=timeout_seconds)
        except concurrent.futures.TimeoutError:
            timed_out = True
            executor.shutdown(wait=False)
        finally:
            fairpyx_root.removeHandler(log_handler)
            fairpyx_root.setLevel(prev_level)

        logs = log_stream.getvalue()

        if timed_out:
            db.session.add(AllocationResult(
                survey_id=survey.id,
                category=category,
                algorithm=algorithm,
                result_json=None,
                logs=logs or None,
            ))
            db.session.commit()
            display = entry['display_name']
            flash(f'"{display}" timed out after {timeout_seconds}s. Partial logs saved.', 'warning')
            return redirect(url_for('survey_results', survey_id=survey.id))

        items = survey.items.all()
        result_data = {
            'allocation':    dict(allocation),
            'algorithm':     algorithm,
            'participants':  [p.get_display_name() for p in participants_with_rankings],
            'items':         [item.name for item in items],
        }

        db.session.add(AllocationResult(
            survey_id=survey.id,
            category=category,
            algorithm=algorithm,
            result_json=json.dumps(result_data),
            logs=logs or None,
        ))
        db.session.commit()

        flash(f'"{entry["display_name"]}" completed successfully!', 'success')
        return redirect(url_for('survey_results', survey_id=survey.id))

    except Exception as e:
        flash(f'Error running algorithm: {str(e)}', 'danger')
        return redirect(url_for('survey_edit', survey_id=survey.id))


@app.route('/surveys/<int:survey_id>/results')
@auth_required()
def survey_results(survey_id):
    """View allocation results for a survey."""
    survey = Survey.query.get_or_404(survey_id)
    if survey.creator_id != current_user.id and not current_user.has_role('admin'):
        abort(403)

    results = survey.allocation_results.order_by(AllocationResult.created_at.desc()).all()
    parsed_results = []
    for r in results:
        parsed_results.append({
            'id':           r.id,
            'category':     r.category,
            'algorithm':    r.algorithm,
            'display_name': ALGORITHMS.get(r.algorithm, {}).get('display_name', r.algorithm.replace('_', ' ').title()),
            'created_at':   r.created_at,
            'data':         json.loads(r.result_json) if r.result_json else {},
            'logs':         r.logs,
        })

    return render_template('survey/results.html', survey=survey, results=parsed_results)


@app.route('/surveys/<int:survey_id>/results/<int:result_id>/delete', methods=['POST'])
@auth_required()
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
            if user_weight is None or not is_valid_weight(user_weight):
                flash('Weight must be a whole number or half (e.g. 1, 1.5, 2, 2.5).', 'danger')
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
        elif survey.ranking_mode == 'approval':
            for item in items:
                approved = request.form.get(f'approved_{item.id}') == 'on'
                db.session.add(ItemRanking(
                    participant_id=participant.id,
                    item_id=item.id,
                    points=1 if approved else 0,
                ))
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
            'allocated_items': explained_items,
            'total_value': total_value
        })

    return render_template('survey/my_results.html', survey=survey, participant=participant, user_results=user_results)


@app.route('/my-surveys')
@auth_required()
def my_surveys():
    """List surveys the current user is participating in."""
    participations = SurveyParticipant.query.filter_by(user_id=current_user.id).all()
    return render_template('survey/my_surveys.html', participations=participations)


def run_migrations():
    """Apply any schema changes that db.create_all() won't handle (ALTER TABLE)."""
    with db.engine.connect() as conn:
        for table, column, col_type in [
            ('allocation_result', 'category',         'VARCHAR(50)'),
            ('survey',            'category',         'VARCHAR(50)'),
            ('user',              'is_system_dummy',  'BOOLEAN DEFAULT 0'),
        ]:
            cols = [row[1] for row in conn.execute(db.text(f"PRAGMA table_info({table})"))]
            if column not in cols:
                conn.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()


def create_system_dummies(count=1000):
    """Ensure exactly `count` permanent system dummy users exist in the DB."""
    existing = User.query.filter_by(is_system_dummy=True).count()
    needed = count - existing
    if needed <= 0:
        return
    # Hash the placeholder password only once — dummies can never log in (active=False)
    dummy_pw = hash_password('__system_dummy_account__')
    for i in range(existing + 1, existing + needed + 1):
        db.session.add(User(
            username=f'__dummy_{i:04d}__',
            email=f'dummy_{i:04d}@system.internal',
            password=dummy_pw,
            fs_uniquifier=str(uuid.uuid4()),
            active=False,
            is_system_dummy=True,
        ))
    db.session.commit()


def grant_moderator_to_existing_users():
    """Give the moderator role to every real user who doesn't have it yet."""
    mod_role = user_datastore.find_role('moderator')
    if not mod_role:
        return
    for user in User.query.filter_by(is_system_dummy=False, active=True).all():
        if not user.has_role('moderator') and not user.has_role('admin'):
            user_datastore.add_role_to_user(user, mod_role)
    db.session.commit()


with app.app_context():
    db.create_all()
    run_migrations()
    create_roles()
    create_admin_user()
    grant_moderator_to_existing_users()
    create_system_dummies(1000)

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5032)
