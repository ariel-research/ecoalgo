import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Flask-Security settings
    SECURITY_PASSWORD_SALT = os.environ.get('SECURITY_PASSWORD_SALT') or 'super-secret-salt-change-in-production'
    SECURITY_PASSWORD_HASH = 'bcrypt'

    # Features
    SECURITY_REGISTERABLE = True
    SECURITY_CONFIRMABLE = False
    SECURITY_RECOVERABLE = False
    SECURITY_CHANGEABLE = True

    # URLs
    SECURITY_LOGIN_URL = '/login'
    SECURITY_LOGOUT_URL = '/logout'
    SECURITY_REGISTER_URL = '/register'
    SECURITY_POST_LOGIN_VIEW = '/'
    SECURITY_POST_LOGOUT_VIEW = '/'
    SECURITY_POST_REGISTER_VIEW = '/'

    # Messages
    SECURITY_MSG_LOGIN = ('Please log in to access this page.', 'info')
    SECURITY_MSG_UNAUTHORIZED = ('You do not have permission to view this resource.', 'danger')

    # CSRF configuration for Flask-Security
    WTF_CSRF_ENABLED = True
    WTF_CSRF_CHECK_DEFAULT = False
    SECURITY_CSRF_PROTECT_MECHANISMS = ['session', 'basic']

    # Send password reset/confirmation without external mail server
    SECURITY_SEND_REGISTER_EMAIL = False
    SECURITY_SEND_PASSWORD_CHANGE_EMAIL = False

    # Algorithm execution timeout in seconds (default: 60)
    ALGORITHM_TIMEOUT = int(os.environ.get('ALGORITHM_TIMEOUT', 60))


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///fair_division.db'


class ProductionConfig(Config):
    DEBUG = False
    # Set DATABASE_URL env var to your MySQL connection string, e.g.:
    # mysql+pymysql://user:password@host:3306/fair_division
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SECURITY_PASSWORD_SALT = os.environ.get('SECURITY_PASSWORD_SALT')


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
