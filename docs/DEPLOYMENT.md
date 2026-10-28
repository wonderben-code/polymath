# Deployment and recovery

Status: D1/D4 release design, 28 October 2026. Local containers, native-adapter
mock tests and browser journeys are verified separately in [PROGRESS.md](PROGRESS.md).
No host, paid plan, domain, public URL or safe production restore is established.
The owner still forbids paid Meta calls. This document grants no spending or
Zenodo publication authority.

## Production topology

Keep the existing application and execution engine. The first release needs one
PostgreSQL database with durable storage, an HTTPS reverse proxy, the built API/UI
image, and explicitly selected worker processes from that same image.

| Process | Responsibility and boundary |
|---|---|
| API/UI | Session ownership, bounded requests/streams, saved reads, explicit enqueue and human controls; no scientific model call in HTTP handlers |
| Native research worker | Existing Ask/Explore/Surprise/artifact/reassessment controllers; one running native job per account, bounded queue and unchanged ledger |
| Literature worker | Bounded free-source collection, shared provider pacing and unassessed versions; no automatic paid reassessment |
| Publication worker | Exact approved document/destination and one-use mutation grants; disabled until separately authorized |
| PostgreSQL | Jobs, source versions, immutable proofs, funding/grant history and resource controls |
| HTTPS proxy | Trusted transport identity, certificate renewal, correct origins and unbuffered/disconnect-aware SSE |

The current `compose.yaml` is a local development/package check. It exposes the
database on loopback and starts only database, migrations and API. A production
configuration must keep PostgreSQL private, persist its data independently of
container replacement, restrict administrative credentials and expose HTTPS.
Do not provision a host until its actual plan, persistent storage, backup costs
and account authorization have been reviewed.

Preserve digest-pinned images and locked dependencies. Serve the built UI/API
from one origin. Run migrations as a separate controlled step and require exact
schema readiness before traffic or workers. Use a single intended database;
an accidental second database must not create a fresh funded wallet.

Keep model/publisher/bootstrap credentials in the host's protected configuration.
Never commit them, put them in image layers, expose them to the browser or log
headers/bodies. The injected cloud package-proxy CA is build-time trust material;
it is not a replacement for production HTTPS validation. Outgoing Meta/Zenodo
destinations remain fixed and redirects remain disabled.

## Launch and configuration rollout

All execution gates start false, including native probe/research, public funding,
publication and collection. Public resource controls can be enabled independently
for a read-only site with a strong server credential. A flag, key, passed canary,
deployment or restored approval never substitutes for current authority.

Before changing roles, server policy or credentials:

1. Disable the shared native account or revoke its activation, committing a new
   database control epoch. Record and verify the stopped state.
2. Stop old workers. An already granted request may finish and must still be
   accounted; unknown work remains held and cannot be retried.
3. Replace processes/configuration and run required migrations. Preserve the
   original pricing, immutable requests and history for known-call recovery.
4. Verify readiness and ownership with all spending gates off. Only an explicit
   newly reviewed activation can rearm funding.

Per-process configuration cannot instantly revoke a worker retaining old settings.
The database epoch is the common fence. Ordinary logout does not revoke an
explicit durable public delegation; its exact deadline and Stop control do.
Read [PUBLIC_RESEARCH_ACCESS.md](PUBLIC_RESEARCH_ACCESS.md) before enabling it.

Pin the actual reverse-proxy trust configuration. Do not accept arbitrary
`Forwarded`/`X-Forwarded-For` as visitor identity. Verify canonical peer grouping,
Secure cookies, exact Origin/CSRF behavior, 64 KiB scientific-body limits,
read/submit/session quotas, stream concurrency and cursor-preserving reconnects
against the real HTTPS endpoint. Proxy buffering and connection timeouts must not
turn stream closure into a false result or silently trigger another job.

Before live science, separately verify the real Meta model/wire, complete billable
usage bounds, current prices, provider credits and cap, then obtain the owner's
paid-test authorization. Run bounded source/model evaluations for the documented
seeds and weak/contradictory input. Synthetic findings and mock invoices remain
labelled as fixtures. Zenodo sandbox/production approval is separate.

## Backup and restore quarantine

A database dump alone is not a verified safe restore. Old snapshots can rewind
revocations, browser/operator expiry, public funding epochs, charged/unknown
reservations and one-use publication grants. Running an old and restored ledger
concurrently can authorize duplicate external effects.

The recovery procedure must first isolate the restored database and processes
from public traffic and external mutation. Keep every worker and spending/
publication flag off. Before starting even a read-only application:

- Freeze and kill every restored budget account, clear its current native scope/
  manifest and advance its local control epoch. Preserve every monetary row.
- Revoke restored operator sessions and expire restored browser capabilities;
  retain their rows and immutable references for audit and known billing.
- Fence pending research/publication work. Retain original grants, observations,
  checkpoints and unknown markers; do not infer that an absent record means an
  external request never happened.
- Keep publication execution disabled. A restored old marker or grant cannot
  establish whether the remote record was already created, uploaded or published.

These are requirements for a future tested quarantine tool, not commands already
implemented. Updating a restored epoch alone cannot recover a newer lost ledger
or prove a globally unique generation. Safe reactivation requires independent
durable evidence of the latest live stop, provider billing/unknown calls and
remote publication effects. Reconcile those facts without resetting counted
spend or releasing unknown holds by assumption. If that evidence is unavailable,
retain read-only quarantine and report paid/publication recovery as blocked.

The old deployment must be fenced and stopped before any restored deployment
could acquire mutation authority. A new login, rotated key, fresh approval or
unfreezing SQL statement is not reconciliation of lost facts.

## Reviewable work before public deployment

Prepare a production Compose/host configuration, explicit epoch-stop rollout,
protected backup export and quarantine-only restore tooling. Keep them separate
from the current local startup and default-off controls. Test with a populated
disposable database containing settled/unknown charges, cancelled stages, expired
and revoked public funding, and previously granted publication work.

Required evidence includes zero new Meta/publisher sends from a restored stale
snapshot, unchanged retained science/export bytes, safe known-call accounting,
rejected old cookies/approvals, and explicit refusal to rearm without reconciled
external facts. Then verify actual host persistence/restarts, HTTPS/proxy identity,
SSE reconnects, funding Stop and source-to-claim journeys. Record the release
commit, image digest, schema and public URL; do not mark hosted deployment or
restoration complete from a local build.

The remaining owner inputs are hosting/account access, domain choice and a
reviewed hosting budget. Provider funding and real-publication approval remain
their own prerequisites. No general user-account/payment system or paid edge
service is assumed for this release.
