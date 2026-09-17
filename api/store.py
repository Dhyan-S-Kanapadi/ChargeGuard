"""Select application persistence without migrating implicitly."""
import os

from api.local_store import InMemoryStore, OrderIdentifierConflictError
from core.runtime import RuntimeConfigurationError, runtime_environment


def store_from_env():
    environment = runtime_environment()
    backend = os.getenv("CHARGEGUARD_STORE_BACKEND", "postgres" if environment == "production" else "local")
    if backend == "local" and environment != "production":
        return InMemoryStore.from_env()
    if backend == "postgres":
        from db.postgres import PostgresStore
        url = os.getenv("DATABASE_URL", "")
        if not url:
            raise RuntimeConfigurationError("DATABASE_URL is required for PostgreSQL storage.")
        return PostgresStore(url, environment=environment)
    raise RuntimeConfigurationError("CHARGEGUARD_STORE_BACKEND must select postgres; local is non-production only.")


store = store_from_env()
