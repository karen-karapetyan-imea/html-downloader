"""Shared pytest fixtures: one MongoDB database, wiped between Mongo-backed tests."""

from __future__ import annotations

import os
import uuid

import pytest

from etl_core.documents import collections_for
from etl_core.store import CRAWLS, ERRORS, STAGE, EntityStore, MongoConfig

MONGO_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
# One DB for the whole session avoids WiredTiger crashes from creating dozens of databases.
SESSION_DB = f"etl_test_{uuid.uuid4().hex[:12]}"
_SESSION_CONFIG = MongoConfig(uri=MONGO_URI, database=SESSION_DB)


@pytest.fixture(scope="session")
def _mongo_ready():
    store = EntityStore(_SESSION_CONFIG, server_selection_timeout_ms=3000)
    try:
        store.client.admin.command("ping")
    except Exception as exc:
        store.close()
        pytest.exit(f"MongoDB is not reachable at {MONGO_URI}: {exc}", returncode=1)
    store.ensure_indexes()
    store.close()
    yield
    closer = EntityStore(_SESSION_CONFIG, server_selection_timeout_ms=3000)
    try:
        closer.client.drop_database(_SESSION_CONFIG.database)
    finally:
        closer.close()


@pytest.fixture
def mongo_config(_mongo_ready) -> MongoConfig:
    """Fresh empty collections for each Mongo-backed test."""
    store = EntityStore(_SESSION_CONFIG, server_selection_timeout_ms=3000)
    _wipe(store)
    store.close()
    return _SESSION_CONFIG


@pytest.fixture
def store(mongo_config: MongoConfig):
    entity_store = EntityStore(mongo_config, server_selection_timeout_ms=3000)
    try:
        yield entity_store
    finally:
        entity_store.close()


def _wipe(store: EntityStore) -> None:
    names = [CRAWLS, STAGE, ERRORS]
    for dataset in ("artists", "artworks", "lots"):
        main, versions = collections_for(dataset)
        names.extend((main, versions))
    for name in names:
        store.db[name].delete_many({})
