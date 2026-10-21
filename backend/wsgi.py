"""WSGI entry point.

Gunicorn (or any WSGI server) imports `application` from this module:

    gunicorn --config gunicorn_conf.py wsgi:application

Kept separate from app.py so the production entry point doesn't drag in the
`if __name__ == "__main__"` development server block, and so the module a
process manager loads is unambiguous.
"""
from app import create_app

application = create_app()

# Some tools look for `app` instead of `application`.
app = application
