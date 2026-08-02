import sqlite3

from flask import Blueprint, jsonify

from database.db import get_db


health_bp = Blueprint("health", __name__)


@health_bp.get("/health")
def health():
    connection = None

    try:
        connection = get_db()
        connection.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return jsonify(
            {
                "status": "unhealthy",
                "database": "unavailable",
            }
        ), 503
    finally:
        if connection is not None:
            connection.close()

    return jsonify(
        {
            "status": "ok",
            "database": "ok",
        }
    ), 200