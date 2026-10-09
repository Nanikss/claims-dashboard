"""Claims analytics dashboard: Flask JSON API + single-page frontend over SQLite."""
from __future__ import annotations

import os
from typing import Any, Mapping

from flask import Flask, render_template

from . import api, cli, db
from .filters import LINES, STATES


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.path.join(app.instance_path, "claims.db"),
    )
    app.json.sort_keys = False  # keep response fields in query order
    if config:
        app.config.from_mapping(config)

    app.teardown_appcontext(db.close_db)
    app.register_blueprint(api.bp)
    cli.register(app)

    @app.get("/")
    def index():
        return render_template("index.html", states=STATES, lines=LINES)

    return app
