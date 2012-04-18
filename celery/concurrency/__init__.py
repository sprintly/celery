# -*- coding: utf-8 -*-
from __future__ import absolute_import

from celery.utils.imports import symbol_by_name

ALIASES = {
    "processes": "celery.concurrency.processes:TaskPool",
    "eventlet": "celery.concurrency.eventlet:TaskPool",
    "gevent": "celery.concurrency.gevent:TaskPool",
    "threads": "celery.concurrency.threads:TaskPool",
    "solo": "celery.concurrency.solo:TaskPool",
}


def get_implementation(cls):
    return symbol_by_name(cls, ALIASES)
