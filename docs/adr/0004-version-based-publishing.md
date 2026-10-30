# ADR 0004: Immutable versions and an atomic pointer swap

Status: accepted

**Decision.** Content and design live in immutable `content_versions` snapshots. A draft is a new
version whose parent is the published one. Publishing changes the `site_state.published_version_id`
pointer and the job state in one transaction. Approval must name the job and the exact draft, and it
re-checks owner, cancellation, state and that the draft's parent is still the live version.

**Why.** Visitors can never see a half-applied change; rejection and cancellation need no cleanup; rollback
(Phase 4) is moving the pointer back. A duplicate approval finds the job already `PUBLISHED` and does nothing.

**Trade-offs.** Snapshots duplicate small amounts of data. No static files are generated: the SPA reads
`/api/site`, which avoids database/filesystem consistency problems.
