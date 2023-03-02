from setuptools import setup, find_packages

setup(name="celery-redis-cluster-backend",
      version='0.2.0',
      description="Celery redis cluster backend",
      license="MIT",
      author="Hasan Basri",
      author_email="hbasria@gmail.com",
      url="http://github.com/hbasria/celery-redis-cluster-backend",
      packages = find_packages(),
      keywords= "celery redis cluster",
      zip_safe = True)