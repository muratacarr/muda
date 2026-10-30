# ADR 0001: Modular monolith on SQLite (WAL)

Status: accepted

Phase 1 is a single-user product on one Raspberry Pi.

**Decision.** One Python codebase split into modules (domain, persistence, ai, worker, bot, api), two
processes (api; bot + worker) and one SQLite database in WAL mode on a shared volume. Writers take the
lock up front with `BEGIN IMMEDIATE`, so concurrent writers queue behind `busy_timeout` instead of failing
while upgrading a read lock.

**Why.** No Redis/Celery/Kafka/Kubernetes is needed to prove the workflow; SQLite gives real
transactions and constraints with no extra service. The job queue is the `jobs` table itself, so a
restart cannot lose work.

**Trade-offs.** Single writer, single worker, no horizontal scaling, and `api` and `bot` must share one
host volume (WAL needs shared memory, not a network filesystem). Moving to PostgreSQL later only touches
`persistence/`; services use SQLAlchemy and plain transactions.
