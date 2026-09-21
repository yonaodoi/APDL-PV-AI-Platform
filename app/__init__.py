from datetime import datetime, timezone

from flask import Flask, session

from config import Config
from .cli import register_cli
from .db import close_db
from .extensions import csrf
from .cases.ai_report_routes import bp as case_ai_reports_blueprint


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    if not app.config.get("TESTING"):
        config_class.validate()

    app.config["UPLOAD_ROOT"].mkdir(parents=True, exist_ok=True)

    csrf.init_app(app)
    app.teardown_appcontext(close_db)
    register_cli(app)

    from .administration.routes import bp as administration_blueprint
    from .attachments.routes import bp as attachments_blueprint
    from .auth.routes import bp as auth_blueprint
    from .cases.routes import bp as cases_blueprint
    from .cases.report_routes import bp as case_reports_blueprint
    from .cases.review_routes import bp as case_review_blueprint
    from .complaints.routes import bp as complaints_blueprint
    from .rsi.routes import bp as rsi_blueprint
    from .signals.routes import bp as signals_blueprint
    from .psur.routes import bp as psur_blueprint
    from .psur.report_routes import bp as psur_reports_blueprint
    from .psur.section_routes import bp as psur_sections_blueprint
    from .psur.builder_routes import bp as psur_builder_blueprint
    from .core.routes import bp as core_blueprint

    app.register_blueprint(administration_blueprint)
    app.register_blueprint(attachments_blueprint)
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(cases_blueprint)
    app.register_blueprint(case_reports_blueprint)
    app.register_blueprint(case_review_blueprint)
    app.register_blueprint(complaints_blueprint)
    app.register_blueprint(rsi_blueprint)
    app.register_blueprint(signals_blueprint)
    app.register_blueprint(psur_blueprint)
    app.register_blueprint(psur_reports_blueprint)
    app.register_blueprint(psur_sections_blueprint)
    app.register_blueprint(psur_builder_blueprint)
    app.register_blueprint(core_blueprint)
    app.register_blueprint(case_ai_reports_blueprint)

    @app.before_request
    def refresh_session_activity():
        if session.get("user_id"):
            session.permanent = True
            session["last_activity_at"] = datetime.now(timezone.utc).isoformat()

    return app