# Project deletion

Project deletion enters `ErasureWorkflow` through `ProjectService.delete`. The
`request_erasure` service method delegates to the same operation. Both require a
confirmation and an idempotency key and return the durable Erasure Request.
Neither method physically deletes records or creates legacy project erasure rows.

The coordinator owns Team Owner authorization, exact project-name confirmation,
scoped idempotency, and the transaction that revokes access and records the
inventory, per-store steps, and AI erasure job. The service must not load the
project first: revoked or physically deleted projects still support authorized
replay through the durable request.

Both project DELETE routes return `202`, the Erasure Request, and its status URL
in `Location`. The team-scoped route passes its expected Team ID to the
coordinator so a mismatched parent remains concealed. Clients poll that URL for
completion; acceptance does not mean physical deletion has finished.

The coordinator cancels pending work and waits for AI cleanup, then purges
objects, generated PDFs, Redis entries, and backend records. Failed steps remain
retryable and completed requests retain safe completion evidence. Repository
cascade deletion remains a physical cleanup operation, never the user deletion
entry point. Existing legacy project erasure rows remain supported by adoption.

Regression tests cover service deletion and legacy-method delegation with real
persistence, immediate revocation, authorization and confirmation failures,
idempotent replay after purge, per-store completion, and preservation of unrelated
projects. HTTP tests cover both routes and their Erasure Request responses. No
schema migration or public/AI contract change is required.
