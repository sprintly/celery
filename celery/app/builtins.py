# -*- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import with_statement

from celery.utils import uuid

#: global list of functions defining a built-in task.
#: these are called for every app instance to setup built-in task.
_builtin_tasks = []


def builtin_task(constructor):
    """Decorator that specifies that the decorated function is a function
    that generates a built-in task.

    The function will then be called for every new app instance created
    (lazily, so more exactly when the task registry for that app is needed).
    """
    _builtin_tasks.append(constructor)
    return constructor


def load_builtin_tasks(app):
    """Loads the built-in tasks for an app instance."""
    [constructor(app) for constructor in _builtin_tasks]


@builtin_task
def add_backend_cleanup_task(app):
    """The backend cleanup task can be used to clean up the default result
    backend.

    This task is also added do the periodic task schedule so that it is
    run every day at midnight, but :program:`celerybeat` must be running
    for this to be effective.

    Note that not all backends do anything for this, what needs to be
    done at cleanup is up to each backend, and some backends
    may even clean up in realtime so that a periodic cleanup is not necessary.

    """
    return app.task(name="celery.backend_cleanup")(app.backend.cleanup)


@builtin_task
def add_unlock_chord_task(app):
    """The unlock chord task is used by result backends that doesn't
    have native chord support.

    It creates a task chain polling the header for completion.

    """
    from celery.canvas import subtask
    from celery import result as _res

    @app.task(name="celery.chord_unlock", max_retries=None)
    def unlock_chord(setid, callback, interval=1, propagate=False,
            max_retries=None, result=None):
        result = _res.TaskSetResult(setid, map(_res.AsyncResult, result))
        j = result.join_native if result.supports_native_join else result.join
        if result.ready():
            subtask(callback).delay(j(propagate=propagate))
        else:
            unlock_chord.retry(countdown=interval, max_retries=max_retries)

    return unlock_chord


@builtin_task
def add_group_task(app):
    from celery.canvas import subtask
    from celery.app.state import get_current_task
    from celery.result import from_serializable

    class Group(app.Task):
        name = "celery.group"
        accept_magic_kwargs = False

        def run(self, tasks, result):
            app = self.app
            result = from_serializable(result)
            with app.pool.acquire(block=True) as conn:
                with app.amqp.TaskPublisher(conn) as publisher:
                    res_ = [subtask(task).apply_async(
                                        taskset_id=self.request.taskset,
                                        publisher=publisher)
                                for task in tasks]
            parent = get_current_task()
            if parent:
                parent.request.children.append(result)
            if self.request.is_eager or app.conf.CELERY_ALWAYS_EAGER:
                return app.TaskSetResult(result.id, res_)
            return result

        def prepare(self, options, tasks, **kwargs):
            r = []
            options["taskset_id"] = group_id = \
                    options.setdefault("task_id", uuid())
            for task in tasks:
                tid = task.options.setdefault("task_id", uuid())
                task.options["taskset_id"] = group_id
                r.append(self.AsyncResult(tid))
            return tasks, self.app.TaskSetResult(group_id, r)

        def apply_async(self, args=(), kwargs={}, **options):
            if self.app.conf.CELERY_ALWAYS_EAGER:
                return self.apply(args, kwargs, **options)
            tasks, result = self.prepare(options, **kwargs)
            super(Group, self).apply_async((tasks, result), **options)
            return result

        def apply(self, args=(), kwargs={}, **options):
            tasks, result = self.prepare(options, **kwargs)
            return super(Group, self).apply((tasks, result), {"eager": True},
                                            **options)

    return Group


@builtin_task
def add_chain_task(app):
    from celery.canvas import maybe_subtask

    class Chain(app.Task):
        name = "celery.chain"
        accept_magic_kwargs = False

        def apply_async(self, args=(), kwargs={}, **options):
            if self.app.conf.CELERY_ALWAYS_EAGER:
                return self.apply(args, kwargs, **options)
            tasks = kwargs["tasks"]
            tasks = [maybe_subtask(task).clone(task_id=uuid(), **kwargs)
                        for task in kwargs["tasks"]]
            reduce(lambda a, b: a.link(b), tasks)
            tasks[0].apply_async()
            results = [task.type.AsyncResult(task.options["task_id"])
                            for task in tasks]
            reduce(lambda a, b: a.set_parent(b), reversed(results))
            return results[-1]

    return Chain


@builtin_task
def add_chord_task(app):
    """Every chord is executed in a dedicated task, so that the chord
    can be used as a subtask, and this generates the task
    responsible for that."""
    from celery import group
    from celery.canvas import maybe_subtask

    class Chord(app.Task):
        name = "celery.chord"
        accept_magic_kwargs = False
        ignore_result = True

        def run(self, header, body, interval=1, max_retries=None,
                propagate=False, eager=False, **kwargs):
            if not isinstance(header, group):
                header = group(header)
            r = []
            setid = uuid()
            for task in header.tasks:
                tid = task.options.setdefault("task_id", uuid())
                task.options["chord"] = body
                r.append(app.AsyncResult(tid))
            app.backend.on_chord_apply(setid, body,
                                       interval=interval,
                                       max_retries=max_retries,
                                       propagate=propagate,
                                       result=r)
            return header(taskset_id=setid)

        def apply_async(self, args=(), kwargs={}, task_id=None, **options):
            if self.app.conf.CELERY_ALWAYS_EAGER:
                return self.apply(args, kwargs, **options)
            body = maybe_subtask(kwargs["body"])

            callback_id = body.options.setdefault("task_id", task_id or uuid())
            super(Chord, self).apply_async(args, kwargs, **options)
            return self.AsyncResult(callback_id)

        def apply(self, args=(), kwargs={}, **options):
            body = kwargs["body"]
            res = super(Chord, self).apply(args, kwargs, **options)
            return maybe_subtask(body).apply(args=(res.get().join(), ))

    return Chord
