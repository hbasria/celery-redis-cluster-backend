# celery-backends-rediscluster

[Celery](http://www.celeryproject.org/)'s custom result backend for [RedisCluster].

## Usage

1. pip install -e git+git://github.com/hbasria/celery-redis-cluster-backend.git#egg=celery-redis-cluster-backend

2. Add the following to `celeryconfig.py`.

```
CELERY_RESULT_BACKEND = "celery_redis_cluster_backend.redis_cluster.RedisClusterBackend"
CELERY_REDIS_CLUSTER_SETTINGS = { 'startup_nodes': [
    {"host": "localhost", "port": "6379"},
    {"host": "localhost", "port": "6380"},
    {"host": "localhost", "port": "6381"}
]}
```

## example usag

start all containers

```
docker compose up -d
```

create and activate python environment 

```
python -m venv .venv
source .venv/bin/activate
```

install all requirements

```
pip install -r requirments.txt
pip install .
```

start celery worker

```
celery -A example worker -B --loglevel=INFO
```

