"""
Shared Flask extension instances.

These live in their own module (instead of inside app.py or models.py)
specifically so that blueprint modules and models.py can both import
`db` / `jwt` without creating circular imports:

    app.py        -> creates the Flask app, calls db.init_app(app)
    models.py     -> imports `db` from here to define models
    */routes.py   -> import `db` (for queries) and model classes from models.py
"""
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager

db = SQLAlchemy()
jwt = JWTManager()
