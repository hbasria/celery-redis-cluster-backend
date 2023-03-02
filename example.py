from celery import Celery

app = Celery('hello', backend='celery_redis_cluster_backend.redis_cluster.RedisClusterBackend')

@app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Calls test('hello') every 10 seconds.
    sender.add_periodic_task(10.0, hello.s('world'), name='add every 10')


@app.task
def hello(arg):
    message = f'hello {arg}'
    print(message)
    return message