from alembic import context
from sqlalchemy import create_engine, pool

url = context.config.get_main_option("sqlalchemy.url")
assert url
connectable = create_engine(url, poolclass=pool.NullPool)
try:
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()
finally:
    connectable.dispose()
