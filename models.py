from flask_sqlalchemy import SQLAlchemy
from flask_security import UserMixin, RoleMixin
from datetime import datetime
import secrets

db = SQLAlchemy()

# Association table for users and roles (many-to-many)
roles_users = db.Table(
    'roles_users',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'))
)


class Role(db.Model, RoleMixin):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True)
    description = db.Column(db.String(255))

    def __repr__(self):
        return f'<Role {self.name}>'


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=True, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)

    # Flask-Security required fields
    active = db.Column(db.Boolean, default=True)
    fs_uniquifier = db.Column(db.String(64), unique=True, nullable=False)

    # Tracking fields (optional but useful)
    confirmed_at = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    current_login_at = db.Column(db.DateTime)
    last_login_ip = db.Column(db.String(100))
    current_login_ip = db.Column(db.String(100))
    login_count = db.Column(db.Integer)

    # Relationships
    roles = db.relationship(
        'Role',
        secondary=roles_users,
        backref=db.backref('users', lazy='dynamic')
    )

    def has_role(self, role):
        """Check if user has a specific role."""
        if isinstance(role, str):
            return role in [r.name for r in self.roles]
        return role in self.roles

    def __repr__(self):
        return f'<User {self.email}>'


class Survey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)

    # Survey creator (moderator)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    creator = db.relationship('User', backref=db.backref('created_surveys', lazy='dynamic'))

    # Unique invite code for the survey
    invite_code = db.Column(db.String(32), unique=True, nullable=False, index=True)

    # Ranking mode: 'ordinal', 'budget', or 'rating'
    ranking_mode = db.Column(db.String(20), default='ordinal')
    # Total points available (only used if ranking_mode is 'budget')
    total_points = db.Column(db.Integer, default=100)
    # Min/max scores for rating mode
    min_score = db.Column(db.Integer, default=1)
    max_score = db.Column(db.Integer, default=10)
    # Whether to use weights (both item weights and user weights)
    use_weights = db.Column(db.Boolean, default=False)

    # Whether users must fill their own capacity
    require_user_capacity = db.Column(db.Boolean, default=False)

    # Whether to use item capacities (if False, treat all items as unlimited capacity)
    use_item_capacity = db.Column(db.Boolean, default=False)

    # Survey state
    is_open = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    items = db.relationship('SurveyItem', backref='survey', lazy='dynamic', cascade='all, delete-orphan')
    participants = db.relationship('SurveyParticipant', backref='survey', lazy='dynamic', cascade='all, delete-orphan')

    def __init__(self, **kwargs):
        super(Survey, self).__init__(**kwargs)
        if not self.invite_code:
            self.invite_code = secrets.token_urlsafe(16)

    def __repr__(self):
        return f'<Survey {self.title}>'


class SurveyItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    survey_id = db.Column(db.Integer, db.ForeignKey('survey.id'), nullable=False)

    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    capacity = db.Column(db.Integer, default=1)
    weight = db.Column(db.Float, default=1.0)

    # Relationships
    rankings = db.relationship('ItemRanking', backref='item', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<SurveyItem {self.name}>'


class SurveyParticipant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    survey_id = db.Column(db.Integer, db.ForeignKey('survey.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # Nullable for dummy users

    # When they joined
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    # User-provided weight and capacity (if required by survey)
    user_weight = db.Column(db.Float)
    user_capacity = db.Column(db.Integer)

    # Dummy user fields
    is_dummy = db.Column(db.Boolean, default=False)
    dummy_name = db.Column(db.String(100), nullable=True)

    # User relationship
    user = db.relationship('User', backref=db.backref('survey_participations', lazy='dynamic'))

    # Rankings for this participant
    rankings = db.relationship('ItemRanking', backref='participant', lazy='dynamic', cascade='all, delete-orphan')

    def get_display_name(self):
        """Return display name for this participant."""
        if self.is_dummy:
            return self.dummy_name
        return self.user.email if self.user else 'Unknown'

    def __repr__(self):
        name = self.dummy_name if self.is_dummy else (self.user.email if self.user else 'Unknown')
        return f'<SurveyParticipant {name} in {self.survey.title}>'


class ItemRanking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.Integer, db.ForeignKey('survey_participant.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('survey_item.id'), nullable=False)

    # For ordinal ranking (1st, 2nd, 3rd, etc.)
    rank = db.Column(db.Integer)
    # For budget-based ranking (distribute total points)
    points = db.Column(db.Integer, default=0)
    # For rating mode (score each item independently)
    rating = db.Column(db.Integer)

    # Unique constraint: participant can only rank an item once
    __table_args__ = (db.UniqueConstraint('participant_id', 'item_id', name='unique_item_ranking'),)

    def __repr__(self):
        return f'<ItemRanking item={self.item_id} rank={self.rank} points={self.points} rating={self.rating}>'


class AllocationResult(db.Model):
    """Stores results from running fairpyx algorithms."""
    id = db.Column(db.Integer, primary_key=True)
    survey_id = db.Column(db.Integer, db.ForeignKey('survey.id'), nullable=False)
    algorithm = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    result_json = db.Column(db.Text)  # JSON allocation data

    survey = db.relationship('Survey', backref=db.backref('allocation_results', lazy='dynamic', cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<AllocationResult {self.algorithm} for survey {self.survey_id}>'
