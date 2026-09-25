import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    db_url = os.environ.get("DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "budget.db"))
    # Render (and most providers) hand out postgres:// but SQLAlchemy needs postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB upload cap

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from .auth import auth_bp
    from .main import main_bp
    from .admin import admin_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)

    with app.app_context():
        db.create_all()
        _run_light_migrations()

    return app


def _run_light_migrations():
    """db.create_all() only creates tables that don't exist yet -- it won't
    add new columns to a table that's already there (like the existing
    production database). This adds any columns introduced after the first
    deploy, so the app keeps working without a manual migration step."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    wanted_columns = {
        "transaction": [("account_id", "INTEGER")],
        "upload_log": [("account_id", "INTEGER")],
    }
    for table, columns in wanted_columns.items():
        if table not in inspector.get_table_names():
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for col_name, col_type in columns:
            if col_name not in existing:
                with db.engine.begin() as conn:
                    # quote the table name: "transaction" is a reserved SQL keyword
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {col_name} {col_type}'))
