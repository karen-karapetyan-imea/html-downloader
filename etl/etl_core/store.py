"""MongoDB persistence: crawl ledger, chunk staging, version publish, conditional mains."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient, ReplaceOne, UpdateOne
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from etl_core import __version__
from etl_core.documents import (
    SortField,
    collections_for,
    crawl_date_str,
    crawl_ledger_id,
    decode_data,
    entity_id,
    entity_type_for,
    pick_winner_docs,
    row_to_data,
    seen_at,
    stage_row_id,
    version_id,
)

log = logging.getLogger(__name__)

DEFAULT_URI = "mongodb://localhost:27017"
DEFAULT_DATABASE = "art_market"
BULK_SIZE = 1000

CRAWLS = "etl_crawls"
STAGE = "etl_stage"
ERRORS = "etl_errors"

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MongoConfig:
    uri: str = DEFAULT_URI
    database: str = DEFAULT_DATABASE

    @classmethod
    def from_env(
        cls, uri: str | None = None, database: str | None = None
    ) -> MongoConfig:
        return cls(
            uri=uri or os.environ.get("MONGODB_URI") or DEFAULT_URI,
            database=database or os.environ.get("MONGODB_DATABASE") or DEFAULT_DATABASE,
        )


@dataclass(frozen=True, slots=True)
class CrawlIdentity:
    dataset: str
    platform: str
    data_dir: str
    chunk_size: int


@dataclass(slots=True)
class ChunkStats:
    index: int
    files: int = 0
    rows: int = 0
    skipped: int = 0
    no_id: int = 0
    errors: list[dict[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class EntityStore:
    """One Mongo database for all datasets; collection names are derived from the dataset."""

    def __init__(self, config: MongoConfig, *, server_selection_timeout_ms: int = 5000) -> None:
        self.config = config
        self.client = MongoClient(
            config.uri,
            tz_aware=True,
            tzinfo=UTC,
            serverSelectionTimeoutMS=server_selection_timeout_ms,
        )
        self.db: Database = self.client[config.database]
        self._indexes_ready = False

    def close(self) -> None:
        self.client.close()

    def ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        for main, versions in (
            collections_for("artists"),
            collections_for("artworks"),
            collections_for("lots"),
        ):
            self.db[main].create_index(
                [("source", ASCENDING), ("platform_id", ASCENDING)], unique=True
            )
            self.db[main].create_index([("source", ASCENDING), ("latest_version", ASCENDING)])
            self.db[versions].create_index(
                [("entity_id", ASCENDING), ("crawl_date", ASCENDING)], unique=True
            )
            self.db[versions].create_index(
                [("entity_id", ASCENDING), ("crawl_date", DESCENDING)]
            )
            self.db[versions].create_index([("source", ASCENDING), ("crawl_date", ASCENDING)])
        # Matches finalize filter+sort so Atlas does not spill a 32MB in-memory sort.
        self.db[STAGE].create_index(
            [("crawl_id", ASCENDING), ("platform_id", ASCENDING), ("chunk_index", ASCENDING)]
        )
        self.db[STAGE].create_index([("crawl_id", ASCENDING), ("chunk_index", ASCENDING)])
        self.db[CRAWLS].create_index([("dataset", ASCENDING), ("source", ASCENDING), ("status", ASCENDING)])
        self.db[ERRORS].create_index([("crawl_id", ASCENDING), ("filename", ASCENDING)])
        self._indexes_ready = True

    def crawls(self) -> Collection:
        return self.db[CRAWLS]

    def stage(self) -> Collection:
        return self.db[STAGE]

    def errors(self) -> Collection:
        return self.db[ERRORS]

    def main(self, dataset: str) -> Collection:
        return self.db[collections_for(dataset)[0]]

    def versions(self, dataset: str) -> Collection:
        return self.db[collections_for(dataset)[1]]

    def get_crawl(self, crawl_id: str) -> dict[str, Any] | None:
        return self.crawls().find_one({"_id": crawl_id})

    def prepare_crawl(
        self,
        identity: CrawlIdentity,
        crawl_date: date,
        *,
        resume: bool,
        parser: Mapping[str, Any],
    ) -> tuple[str, set[int]]:
        """Create or resume a crawl ledger entry. Returns (crawl_id, completed chunk indexes)."""
        self.ensure_indexes()
        crawl_id = crawl_ledger_id(identity.dataset, identity.platform, crawl_date_str(crawl_date))
        identity_doc = {
            "dataset": identity.dataset,
            "platform": identity.platform,
            "data_dir": identity.data_dir,
            "chunk_size": identity.chunk_size,
        }
        existing = self.get_crawl(crawl_id)
        if resume and existing is not None and existing.get("status") != STATUS_COMPLETED:
            previous = {
                "dataset": existing.get("dataset"),
                "platform": existing.get("platform"),
                "data_dir": existing.get("data_dir"),
                "chunk_size": existing.get("chunk_size"),
            }
            if previous != identity_doc:
                raise ResumeMismatchError(
                    f"cannot resume {crawl_id}: it was started with {previous}, now {identity_doc}"
                )
            self.crawls().update_one(
                {"_id": crawl_id},
                {"$set": {"status": STATUS_RUNNING}, "$unset": {"finished_at": "", "stats": ""}},
            )
            return crawl_id, {int(i) for i in existing.get("completed_chunks") or []}

        self.stage().delete_many({"crawl_id": crawl_id})
        self.errors().delete_many({"crawl_id": crawl_id})
        now = datetime.now(UTC)
        self.crawls().replace_one(
            {"_id": crawl_id},
            {
                "_id": crawl_id,
                **identity_doc,
                "source": identity.platform,
                "crawl_date": crawl_date_str(crawl_date),
                "status": STATUS_RUNNING,
                "completed_chunks": [],
                "chunk_stats": [],
                "parser": dict(parser),
                "etl_version": __version__,
                "started_at": now,
                "created_at": existing.get("created_at", now) if existing else now,
                "updated_at": now,
            },
            upsert=True,
        )
        return crawl_id, set()

    def write_chunk(
        self,
        crawl_id: str,
        dataset: str,
        platform: str,
        crawl_date: date,
        chunk_index: int,
        rows: Sequence[object],
        key_column: str,
        order: Sequence[SortField],
        stats: ChunkStats,
        *,
        uri: str | None = None,
    ) -> None:
        """Persist deduped chunk rows into the stage collection and mark the chunk done."""
        from etl_core.documents import dedup_rows, platform_id_of, winner_sort_key

        date_s = crawl_date_str(crawl_date)
        winners = dedup_rows(rows, key_column, order)
        ops: list[ReplaceOne] = []
        for row in winners:
            pid = platform_id_of(row, key_column)
            data = row_to_data(row)
            ops.append(
                ReplaceOne(
                    {"_id": stage_row_id(crawl_id, chunk_index, pid)},
                    {
                        "_id": stage_row_id(crawl_id, chunk_index, pid),
                        "crawl_id": crawl_id,
                        "dataset": dataset,
                        "source": platform,
                        "crawl_date": date_s,
                        "chunk_index": chunk_index,
                        "platform_id": pid,
                        "entity_id": entity_id(platform, pid),
                        "data": data,
                        "sort_key": list(_bson_sort_key(winner_sort_key(row, order))),
                        "source_file": data.get("source_file"),
                        "crawled_at": data.get("crawled_at"),
                    },
                    upsert=True,
                )
            )
        if ops:
            for batch in _batched(ops, BULK_SIZE):
                self.stage().bulk_write(batch, ordered=False)

        error_ops = [
            ReplaceOne(
                {"_id": f"{crawl_id}:{chunk_index:05d}:{err['filename']}"},
                {
                    "_id": f"{crawl_id}:{chunk_index:05d}:{err['filename']}",
                    "crawl_id": crawl_id,
                    "chunk_index": chunk_index,
                    "filename": err["filename"],
                    "error": err["error"],
                },
                upsert=True,
            )
            for err in stats.errors
        ]
        if error_ops:
            for batch in _batched(error_ops, BULK_SIZE):
                self.errors().bulk_write(batch, ordered=False)

        self.crawls().update_one(
            {"_id": crawl_id},
            {
                "$addToSet": {"completed_chunks": chunk_index},
                "$pull": {"chunk_stats": {"index": chunk_index}},
                "$set": {"updated_at": datetime.now(UTC)},
            },
        )
        self.crawls().update_one(
            {"_id": crawl_id},
            {
                "$push": {
                    "chunk_stats": {
                        "index": chunk_index,
                        "files": stats.files,
                        "rows": stats.rows,
                        "skipped": stats.skipped,
                        "no_id": stats.no_id,
                        "errors": len(stats.errors or []),
                    }
                }
            },
        )

    def finalize_crawl(
        self,
        crawl_id: str,
        dataset: str,
        platform: str,
        crawl_date: date,
        order: Sequence[SortField],
        *,
        parser_fingerprint: str,
        run_info: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Dedup staged rows across chunks, publish versions, conditionally update mains."""
        date_s = crawl_date_str(crawl_date)
        write_token = uuid.uuid4().hex
        now = datetime.now(UTC)
        main = self.main(dataset)
        versions = self.versions(dataset)
        entity_type = entity_type_for(dataset)

        winners = list(self._iter_stage_winners(crawl_id, order))
        published_ids: set[str] = set()
        version_ops: list[UpdateOne] = []
        main_ops: list[UpdateOne] = []

        for doc in winners:
            pid = doc["platform_id"]
            eid = entity_id(platform, pid)
            vid = version_id(eid, date_s)
            data = doc["data"]
            crawled = data.get("crawled_at")
            source_file = data.get("source_file")
            published_ids.add(vid)

            version_ops.append(
                UpdateOne(
                    {"_id": vid},
                    {
                        "$set": {
                            "entity_id": eid,
                            "source": platform,
                            "platform_id": pid,
                            "crawl_date": date_s,
                            "data": data,
                            "source_file": source_file,
                            "crawled_at": crawled,
                            "parser_fingerprint": parser_fingerprint,
                            "etl_version": __version__,
                            "write_token": write_token,
                            "updated_at": now,
                        },
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
            )

            observed = seen_at(crawled if isinstance(crawled, datetime) else None, date_s)
            main_ops.append(
                UpdateOne(
                    {
                        "_id": eid,
                        "$or": [
                            {"latest_version": {"$exists": False}},
                            {"latest_version": None},
                            {"latest_version": {"$lte": date_s}},
                        ],
                    },
                    {
                        "$set": {
                            "source": platform,
                            "platform_id": pid,
                            "entity_type": entity_type,
                            "data": data,
                            "latest_version": date_s,
                            "last_seen_at": observed,
                            "updated_at": now,
                        },
                        "$setOnInsert": {
                            "first_seen_at": observed,
                            "created_at": now,
                        },
                    },
                    upsert=True,
                )
            )

        for batch in _batched(version_ops, BULK_SIZE):
            versions.bulk_write(batch, ordered=False)
        for batch in _batched(main_ops, BULK_SIZE):
            self._bulk_main_updates(main, batch)

        stale = versions.delete_many(
            {
                "source": platform,
                "crawl_date": date_s,
                "write_token": {"$ne": write_token},
            }
        )
        if stale.deleted_count:
            self._repair_mains_after_version_delete(dataset, platform, date_s)

        self.stage().delete_many({"crawl_id": crawl_id})

        error_count = self.errors().count_documents({"crawl_id": crawl_id})
        stats = {
            "rows": len(winners),
            "files_total": run_info.get("files_total", 0),
            "skipped": run_info.get("skipped", 0),
            "no_id": run_info.get("no_id", 0),
            "errors": error_count,
            "chunks": run_info.get("chunks", 0),
            "resumed_chunks": run_info.get("resumed_chunks", 0),
        }
        finished = datetime.now(UTC)
        manifest = {
            **dict(run_info),
            **stats,
            "crawl_date": date_s,
            "finished_at": finished,
            "duration_s": round(
                (finished - (run_info.get("started_at") or finished)).total_seconds(), 1
            )
            if isinstance(run_info.get("started_at"), datetime)
            else run_info.get("duration_s"),
            "parser_fingerprint": parser_fingerprint,
            "etl_version": __version__,
            "status": STATUS_COMPLETED,
        }
        self.crawls().update_one(
            {"_id": crawl_id},
            {
                "$set": {
                    "status": STATUS_COMPLETED,
                    "finished_at": finished,
                    "updated_at": finished,
                    "stats": stats,
                    "parser_fingerprint": parser_fingerprint,
                    "etl_version": __version__,
                    **{
                        k: run_info[k]
                        for k in ("html_root", "crawl_log_entries", "started_at")
                        if k in run_info
                    },
                }
            },
        )
        return manifest

    def mark_failed(self, crawl_id: str, error: str) -> None:
        self.crawls().update_one(
            {"_id": crawl_id},
            {
                "$set": {
                    "status": STATUS_FAILED,
                    "error": error[:1000],
                    "updated_at": datetime.now(UTC),
                }
            },
        )

    def compact_source(self, dataset: str, platform: str) -> CompactResult:
        """Rebuild main documents for a source from historical versions (repair)."""
        self.ensure_indexes()
        versions = self.versions(dataset)
        if versions.count_documents({"source": platform}, limit=1) == 0:
            crawl_ok = self.crawls().count_documents(
                {"dataset": dataset, "source": platform, "status": STATUS_COMPLETED}, limit=1
            )
            if not crawl_ok:
                raise FileNotFoundError(f"no finished crawls for {platform} in {dataset}")
        main = self.main(dataset)
        entity_type = entity_type_for(dataset)
        cursor = versions.find({"source": platform}).sort(
            [("entity_id", ASCENDING), ("crawl_date", DESCENDING)]
        )
        current_entity: str | None = None
        rows = 0
        crawl_dates: set[str] = set()
        ops: list[UpdateOne] = []
        now = datetime.now(UTC)
        for doc in cursor:
            crawl_dates.add(doc["crawl_date"])
            eid = doc["entity_id"]
            if eid == current_entity:
                continue
            current_entity = eid
            data = doc["data"]
            observed = seen_at(
                data.get("crawled_at") if isinstance(data.get("crawled_at"), datetime) else None,
                doc["crawl_date"],
            )
            first = versions.find_one(
                {"entity_id": eid},
                sort=[("crawl_date", ASCENDING)],
                projection={"crawl_date": 1, "data.crawled_at": 1},
            )
            first_seen = seen_at(
                (first or {}).get("data", {}).get("crawled_at")
                if isinstance((first or {}).get("data", {}).get("crawled_at"), datetime)
                else None,
                (first or doc)["crawl_date"],
            )
            ops.append(
                UpdateOne(
                    {"_id": eid},
                    {
                        "$set": {
                            "source": platform,
                            "platform_id": doc["platform_id"],
                            "entity_type": entity_type,
                            "data": data,
                            "latest_version": doc["crawl_date"],
                            "last_seen_at": observed,
                            "updated_at": now,
                            "first_seen_at": first_seen,
                            "created_at": first_seen,
                        }
                    },
                    upsert=True,
                )
            )
            rows += 1
            if len(ops) >= BULK_SIZE:
                main.bulk_write(ops, ordered=False)
                ops = []
        if ops:
            main.bulk_write(ops, ordered=False)

        live_ids = set(versions.distinct("entity_id", {"source": platform}))
        obsolete = list(main.find({"source": platform}, projection={"_id": 1}))
        to_delete = [d["_id"] for d in obsolete if d["_id"] not in live_ids]
        if to_delete:
            main.delete_many({"_id": {"$in": to_delete}})

        return CompactResult(
            platform=platform,
            snapshots=len(crawl_dates),
            snapshots_compacted=len(crawl_dates),
            current_rows=rows,
        )

    def publish_rows(
        self,
        dataset: str,
        platform: str,
        crawl_date: date | str,
        rows: Sequence[object],
        key_column: str,
        order: Sequence[SortField],
        *,
        parser_fingerprint: str = "",
    ) -> int:
        """Upsert already-deduped winners for one crawl date (used by Parquet migration)."""
        from etl_core.documents import dedup_rows, platform_id_of

        self.ensure_indexes()
        date_s = crawl_date_str(crawl_date)
        winners = dedup_rows(rows, key_column, order)
        write_token = uuid.uuid4().hex
        now = datetime.now(UTC)
        main = self.main(dataset)
        versions = self.versions(dataset)
        entity_type = entity_type_for(dataset)
        version_ops: list[UpdateOne] = []
        main_ops: list[UpdateOne] = []
        for row in winners:
            pid = platform_id_of(row, key_column)
            eid = entity_id(platform, pid)
            vid = version_id(eid, date_s)
            data = row_to_data(row)
            version_ops.append(
                UpdateOne(
                    {"_id": vid},
                    {
                        "$set": {
                            "entity_id": eid,
                            "source": platform,
                            "platform_id": pid,
                            "crawl_date": date_s,
                            "data": data,
                            "source_file": data.get("source_file"),
                            "crawled_at": data.get("crawled_at"),
                            "parser_fingerprint": parser_fingerprint,
                            "etl_version": __version__,
                            "write_token": write_token,
                            "updated_at": now,
                        },
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
            )
            observed = seen_at(
                data.get("crawled_at") if isinstance(data.get("crawled_at"), datetime) else None,
                date_s,
            )
            main_ops.append(
                UpdateOne(
                    {
                        "_id": eid,
                        "$or": [
                            {"latest_version": {"$exists": False}},
                            {"latest_version": None},
                            {"latest_version": {"$lte": date_s}},
                        ],
                    },
                    {
                        "$set": {
                            "source": platform,
                            "platform_id": pid,
                            "entity_type": entity_type,
                            "data": data,
                            "latest_version": date_s,
                            "last_seen_at": observed,
                            "updated_at": now,
                        },
                        "$setOnInsert": {"first_seen_at": observed, "created_at": now},
                    },
                    upsert=True,
                )
            )
        for batch in _batched(version_ops, BULK_SIZE):
            versions.bulk_write(batch, ordered=False)
        for batch in _batched(main_ops, BULK_SIZE):
            self._bulk_main_updates(main, batch)

        # Mark the crawl ledger so HTML sync does not re-parse this snapshot.
        crawl_id = crawl_ledger_id(dataset, platform, date_s)
        self.crawls().update_one(
            {"_id": crawl_id},
            {
                "$set": {
                    "dataset": dataset,
                    "platform": platform,
                    "source": platform,
                    "crawl_date": date_s,
                    "status": STATUS_COMPLETED,
                    "migrated_from": "parquet",
                    "stats": {"rows": len(winners)},
                    "etl_version": __version__,
                    "parser_fingerprint": parser_fingerprint,
                    "finished_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now, "started_at": now},
            },
            upsert=True,
        )
        return len(winners)

    def find_main(self, dataset: str, source: str, platform_id: str) -> dict[str, Any] | None:
        doc = self.main(dataset).find_one({"_id": entity_id(source, platform_id)})
        if doc and "data" in doc:
            doc["data"] = decode_data(doc["data"])
        return doc

    def list_mains(self, dataset: str, source: str) -> list[dict[str, Any]]:
        docs = list(self.main(dataset).find({"source": source}).sort("_id", ASCENDING))
        for doc in docs:
            if "data" in doc:
                doc["data"] = decode_data(doc["data"])
        return docs

    def count_mains(self, dataset: str, source: str) -> int:
        return self.main(dataset).count_documents({"source": source})

    def list_versions(
        self, dataset: str, source: str, platform_id: str
    ) -> list[dict[str, Any]]:
        eid = entity_id(source, platform_id)
        docs = list(
            self.versions(dataset)
            .find({"entity_id": eid})
            .sort("crawl_date", ASCENDING)
        )
        for doc in docs:
            if "data" in doc:
                doc["data"] = decode_data(doc["data"])
        return docs

    def _iter_stage_winners(
        self, crawl_id: str, order: Sequence[SortField]
    ) -> Iterator[dict[str, Any]]:
        cursor = (
            self.stage()
            .find({"crawl_id": crawl_id})
            .sort([("platform_id", ASCENDING), ("chunk_index", ASCENDING)])
            .allow_disk_use(True)
        )
        group: list[dict[str, Any]] = []
        current: str | None = None
        for doc in cursor:
            pid = doc["platform_id"]
            if current is None:
                current = pid
            if pid != current:
                yield pick_winner_docs(group, order)
                group = []
                current = pid
            group.append(doc)
        if group:
            yield pick_winner_docs(group, order)

    def _bulk_main_updates(self, main: Collection, ops: Sequence[UpdateOne]) -> None:
        from pymongo.errors import BulkWriteError

        try:
            main.bulk_write(list(ops), ordered=False)
        except BulkWriteError:
            # Concurrent upserts can race on _id; retry each op, falling back without upsert.
            for op in ops:
                try:
                    main.bulk_write([op], ordered=True)
                except (BulkWriteError, DuplicateKeyError):
                    main.update_one(op._filter, op._doc, upsert=False)

    def _repair_mains_after_version_delete(
        self, dataset: str, platform: str, crawl_date: str
    ) -> None:
        """When a reprocess drops entities from a crawl date that was latest, rebuild or remove mains."""
        main = self.main(dataset)
        versions = self.versions(dataset)
        # Mains that still claim this crawl date but no longer have that version.
        claimed = list(main.find({"source": platform, "latest_version": crawl_date}))
        now = datetime.now(UTC)
        entity_type = entity_type_for(dataset)
        for doc in claimed:
            eid = doc["_id"]
            latest = versions.find_one({"entity_id": eid}, sort=[("crawl_date", DESCENDING)])
            if latest is None:
                main.delete_one({"_id": eid})
                continue
            if latest["crawl_date"] == crawl_date:
                continue
            data = latest["data"]
            observed = seen_at(
                data.get("crawled_at") if isinstance(data.get("crawled_at"), datetime) else None,
                latest["crawl_date"],
            )
            main.update_one(
                {"_id": eid},
                {
                    "$set": {
                        "source": platform,
                        "platform_id": latest["platform_id"],
                        "entity_type": entity_type,
                        "data": data,
                        "latest_version": latest["crawl_date"],
                        "last_seen_at": observed,
                        "updated_at": now,
                    }
                },
            )


class ResumeMismatchError(ValueError):
    """An interrupted crawl was started with other run settings, so it cannot be resumed."""


@dataclass(frozen=True, slots=True)
class CompactResult:
    platform: str
    snapshots: int
    snapshots_compacted: int
    current_rows: int


def connect(config: MongoConfig | None = None) -> EntityStore:
    return EntityStore(config or MongoConfig.from_env())


def _batched[T](items: Sequence[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def _bson_sort_key(key: tuple[Any, ...]) -> list[Any]:
    """Flatten nested tuples so Mongo can store the key as an array (debug / optional sort)."""
    out: list[Any] = []
    for part in key:
        if isinstance(part, tuple):
            out.append(list(part))
        else:
            out.append(part)
    return out
