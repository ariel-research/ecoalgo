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
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{os.environ.get('DB_USER')}:{os.environ.get('DB_PASSWORD')}"
        f"@{os.environ.get('DB_HOST', 'localhost')}:{os.environ.get('DB_PORT', '3306')}"
        f"/{os.environ.get('DB_NAME', 'fair_division')}"
    )
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SECURITY_PASSWORD_SALT = os.environ.get('SECURITY_PASSWORD_SALT')
    # Recycle connections before MySQL's wait_timeout (default 8h) closes them
    SQLALCHEMY_POOL_RECYCLE = 3600


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
