# -*- coding: utf-8 -*-
"""
    celery.backends.rediscluster
    ~~~~~~~~~~~~~~~~~~~~~

    Redis cluster result store backend.

    CELERY_REDIS_CLUSTER_BACKEND_SETTINGS = {
        startup_nodes: [{"host": "127.0.0.1", "port": "6379"}]
    }
"""
from __future__ import absolute_import

from functools import partial

from kombu.utils import cached_property, retry_over_time

from celery import states
from celery.canvas import maybe_signature
from celery.exceptions import ChordError, ImproperlyConfigured
from celery.utils import strtobool
from celery.utils.log import get_logger
from celery.utils.timeutils import humanize_seconds

from celery.backends.base import KeyValueStoreBackend

#try:
from rediscluster.client import RedisCluster
#from kombu.transport.redis import get_redis_error_classes
#except ImportError:                 # pragma: no cover
#    RedisCluster = None                    # noqa
#    ConnectionError = None          # noqa
get_redis_error_classes = None  # noqa

__all__ = ['RedisClusterBackend']

REDIS_MISSING = """\
You need to install the redis-py-cluster library in order to use \
the Redis result store backend."""

logger = get_logger(__name__)
error = logger.error


class RedisClusterBackend(KeyValueStoreBackend):
    """Redis task result store."""

    #: redis client module.
    redis = RedisCluster

    startup_nodes=None
    max_connections = None
    init_slot_cache=True

    supports_autoexpire = True
    supports_native_join = True
    implements_incr = True

    def __init__(self, *args, **kwargs):
        super(RedisClusterBackend, self).__init__(expires_type=int, **kwargs)
        conf = self.app.conf

        if self.redis is None:
            raise ImproperlyConfigured(REDIS_MISSING)

        # For compatibility with the old REDIS_* configuration keys.
        def _get(key):
            for prefix in 'CELERY_REDIS_{0}', 'REDIS_{0}':
                try:
                    return conf[prefix.format(key)]
                except KeyError:
                    pass      

        self.conn_params = self.app.conf.get('CELERY_REDIS_CLUSTER_SETTINGS', {
            'startup_nodes': [{'host': _get('HOST') or 'localhost', 'port': _get('PORT') or 6379}]
        })

        if self.conn_params is not None:
            if not isinstance(self.conn_params, dict):
                raise ImproperlyConfigured(
                    'RedisCluster backend settings should be grouped in a dict')

        try:
            new_join = strtobool(self.conn_params.pop('new_join'))
            
            if new_join:
                self.apply_chord = self._new_chord_apply
                self.on_chord_part_return = self._new_chord_return

        except KeyError:
            pass

        self.expires = self.prepare_expires(None, type=int)
        self.connection_errors, self.channel_errors = (
            get_redis_error_classes() if get_redis_error_classes
            else ((), ()))

    def get(self, key):
        return self.client.get(key)

    def mget(self, keys):
        return self.client.mget(keys)

    def ensure(self, fun, args, **policy):
        retry_policy = dict(self.retry_policy, **policy)
        max_retries = retry_policy.get('max_retries')
        return retry_over_time(
            fun, self.connection_errors, args, {},
            partial(self.on_connection_error, max_retries),
            **retry_policy
        )

    def on_connection_error(self, max_retries, exc, intervals, retries):
        tts = next(intervals)
        error('Connection to Redis lost: Retry (%s/%s) %s.',
              retries, max_retries or 'Inf',
              humanize_seconds(tts, 'in '))
        return tts

    def set(self, key, value, **retry_policy):
        return self.ensure(self._set, (key, value), **retry_policy)

    def _set(self, key, value):
        if hasattr(self, 'expires'):
            self.client.setex(key, value, self.expires)
        else:
            self.client.set(key, value)

    def delete(self, key):
        self.client.delete(key)

    def incr(self, key):
        return self.client.incr(key)

    def expire(self, key, value):
        return self.client.expire(key, value)

    def add_to_chord(self, group_id, result):
        self.client.incr(self.get_key_for_group(group_id, '.t'), 1)

    def _unpack_chord_result(self, tup, decode,
                             EXCEPTION_STATES=states.EXCEPTION_STATES,
                             PROPAGATE_STATES=states.PROPAGATE_STATES):
        _, tid, state, retval = decode(tup)
        if state in EXCEPTION_STATES:
            retval = self.exception_to_python(retval)
        if state in PROPAGATE_STATES:
            raise ChordError('Dependency {0} raised {1!r}'.format(tid, retval))
        return retval

    def _new_chord_apply(self, header, partial_args, group_id, body,
                         result=None, options={}, **kwargs):
        # avoids saving the group in the redis db.
        options['task_id'] = group_id
        return header(*partial_args, **options or {})

    def _new_chord_return(self, task, state, result, propagate=None,
                          PROPAGATE_STATES=states.PROPAGATE_STATES):
        app = self.app
        if propagate is None:
            propagate = self.app.conf.CELERY_CHORD_PROPAGATES
        request = task.request
        tid, gid = request.id, request.group
        if not gid or not tid:
            return

        client = self.client
        jkey = self.get_key_for_group(gid, '.j')
        tkey = self.get_key_for_group(gid, '.t')
        result = self.encode_result(result, state)
        _, readycount, totaldiff, _, _ = client.pipeline()              \
            .rpush(jkey, self.encode([1, tid, state, result]))          \
            .llen(jkey)                                                 \
            .get(tkey)                                                  \
            .expire(jkey, 86400)                                        \
            .expire(tkey, 86400)                                        \
            .execute()

        totaldiff = int(totaldiff or 0)

        try:
            callback = maybe_signature(request.chord, app=app)
            total = callback['chord_size'] + totaldiff
            if readycount == total:
                decode, unpack = self.decode, self._unpack_chord_result
                resl, _, _ = client.pipeline()  \
                    .lrange(jkey, 0, total)     \
                    .delete(jkey)               \
                    .delete(tkey)               \
                    .execute()
                try:
                    callback.delay([unpack(tup, decode) for tup in resl])
                except Exception as exc:
                    error('Chord callback for %r raised: %r',
                          request.group, exc, exc_info=1)
                    app._tasks[callback.task].backend.fail_from_current_stack(
                        callback.id,
                        exc=ChordError('Callback error: {0!r}'.format(exc)),
                    )
        except ChordError as exc:
            error('Chord %r raised: %r', request.group, exc, exc_info=1)
            app._tasks[callback.task].backend.fail_from_current_stack(
                callback.id, exc=exc,
            )
        except Exception as exc:
            error('Chord %r raised: %r', request.group, exc, exc_info=1)
            app._tasks[callback.task].backend.fail_from_current_stack(
                callback.id, exc=ChordError('Join error: {0!r}'.format(exc)),
            )

    @cached_property
    def client(self):
        return RedisCluster(**self.conn_params)

    def __reduce__(self, args=(), kwargs={}):
        return super(RedisClusterBackend, self).__reduce__(
            (self.conn_params['startup_nodes'], ), {'expires': self.expires},
        )


if __name__ == '__main__':
    from celery import Celery

    class Config:
        CELERY_ENABLE_UTC = True
        CELERY_TIMEZONE = 'Europe/Istanbul'
        CELERY_REDIS_CLUSTER_SETTINGS = { 'startup_nodes': [
            {"host": "195.175.249.97", "port": "6379"},
            {"host": "195.175.249.98", "port": "6379"},
            {"host": "195.175.249.99", "port": "6380"}
        ]}

    
    app = Celery()
    app.config_from_object(Config)

    rb = RedisClusterBackend(app=app)
    rb.set('a', 'b1')

    print rb.get('a')

