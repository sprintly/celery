from __future__ import absolute_import

import threading

from celery.local import Proxy, LocalStack

default_app = None


class _TLS(threading.local):
    #: Apps with the :attr:`~celery.app.base.BaseApp.set_as_current` attribute
    #: sets this, so it will always contain the last instantiated app,
    #: and is the default app returned by :func:`app_or_default`.
    current_app = None
_tls = _TLS()

_task_stack = LocalStack()


def set_default_app(app):
    global default_app
    if default_app is None:
        default_app = app


def get_current_app():
    if default_app is None:
        # creates the default app, but we want to defer that.
        import celery.app  # noqa
    return _tls.current_app or default_app


def get_current_task():
    return _task_stack.top


current_app = Proxy(get_current_app)
current_task = Proxy(get_current_task)
