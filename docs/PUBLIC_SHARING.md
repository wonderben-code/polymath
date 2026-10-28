# Public finding snapshots

This local implementation follows C1 and is controlled by
`PUBLIC_SHARING_ENABLED=false` by default. Only an explicit owner action grants
public access. Native calls, model execution, DOI publication and accounts are
outside this milestone. Local regression and two-browser verification passed; native integrations and public deployment remain separate gates.

An owner explicitly shares one completed, current supported finding. Other
people can read that exact snapshot, find it in a public library and explore its
field/citation map without creating a browser session. Sharing never starts a
worker, retrieves a paper, grants access to the private investigation or calls
Zenodo. No existing finding becomes public automatically.

## Owner flow and authority

The finding view offers **Preview public snapshot**, followed by **Share
publicly**. The preview is the exact server projection, including every claim,
its original uncertainty, all citation links, the short quotation excerpts and
the expiry. For Ask, the original question is visibly part of the finding and
will be public. The separate run goal, hidden context and investigation history
are excluded. The preview also explains that a later assessment can mark this
snapshot superseded or unsupported without exposing any newer private finding.

The Share button is the explicit owner grant. It requires the existing browser
cookie, CSRF token and Origin checks, plus the displayed projection SHA-256.
There is no operator login, separate approval receipt or model authority.

The complete canonical projection is bounded to 262,144 UTF-8 bytes, with the
existing finding text limits preserved. Excerpt caps apply to the structured
citation excerpts; they do not certify that arbitrary model prose contains no
copied phrases or establish licensing compliance or scientific correctness.

A share expires at the browser session's exact `expires_at`, currently at most
seven days after that session was created. It cannot outlive that owner
capability. The owner can revoke it earlier. Losing the browser cookie loses
early revocation access; the announced expiry still limits availability.
Sharing does not create permanent account ownership.

## Immutable data and lock order

Migration `0018_public_sharing` introduces two tables:

| Table | Stored fields |
|---|---|
| `shared_findings` | Private row UUID, unique random public ID, owner-session FK, source result/run/root FKs and source hash, protocol version, immutable public projection JSONB and canonical SHA-256, idempotency key/request hash, frozen expiry, creation time |
| `shared_finding_revocations` | Share FK/primary key, private revoking owner-session hash, revocation time |

Both tables reject UPDATE/DELETE. Revocation is append-only and permanent for
that public ID. A later explicit re-share creates another public ID. Downgrade
refuses to discard share or revocation history. A unique
`(owner_session_hash, idempotency_key)` makes a lost-response retry return the
same grant; changing the request under that key fails. Retrying never revives a
revoked or expired grant. After active-owner authorization, resolve an existing
idempotency key before applying fresh current-source admission: a lost-response
retry must still report its already-created, now historical/revoked grant. It
must not silently create another public handle.

Public IDs contain 32 random bytes encoded as 43 URL-safe characters and are
independent of all investigation, result, source, retrieval and snapshot IDs.
They are public resource identifiers, grant no private or write authority, and
can be listed only after the explicit share action.

The preview is a read-only computation and never initializes or locks a head.
Share creation uses a global sharing-quota advisory lock, then
`FindingHead` → new share row. It uses the existing legacy-head upsert/lock
helper, so the first unsupported assessment cannot race an absent head. Recheck
browser expiry, exact source hash, current-supported eligibility and the
projection hash after waits. Do not acquire ResearchRun, publisher or
publication-job locks. Revoke locks its share row only, then rechecks the owner
and clock. Default bounds are 10 active shares per owner and 1,000 globally;
previews create no durable grant and consume no model budget.

## Public projection

Use new strict DTOs and construct them field by field. Do not return a
`ResearchResult` or sanitize one with a deny list. Do not reuse the private
knowledge/result/source serializers. Public reads consume only the saved public
projection; private head pointers may be consulted solely to compute the status
badge.

The scientific content protocol is `polymath-public-finding-v1`. Its canonical
JSON SHA-256 is immutable. Public activation metadata, including the public ID,
expiry and current-status badge, sits outside that content hash.

The projection includes:

- The complete Bridge finding text/mappings/objection/novelty limitations, or the
  complete Ask question/summary/claims/derivations/caveats/limitations/unanswered
  questions. Original `known`, `postulated` and `speculative` labels remain
  model assessments. Every original citation association is retained.
- For Surprise, only the final selected fields and their final rationale; no
  intermediate selection history.
- Share-local IDs such as `claim-1`, `paper-1` and `citation-1`. Original model
  claim IDs are remapped, and private source identifiers are never copied.
- Scholarly title and bounded bibliography from the cited references, with
  validated DOI/arXiv/OpenAlex identifiers and reconstructed scholarly links.
  Never echo arbitrary stored URLs, including credentials, query tokens,
  fragments or opaque paths. If no validated scholarly identifier is available,
  retain the title/hash with no external link.
- Each verified citation's side/claim association, model support statement,
  original source kind, source/window content SHA-256, full saved-quote length
  and SHA-256, and clearly separated full-quote versus displayed-excerpt Unicode
  offsets.
- For a full-text window, its extracted snapshot SHA-256, arXiv base/version and
  saved window `start`, `end`, `total`. Omit `coverage.snapshot_id`, physical
  window `source_id`, retrieval/version UUIDs and raw corpus text.

The projection excludes original source bodies, full HTML snapshots, unused
sources, run IDs/goals/events, prompts, search queries/results, budgets/usage,
memory-read pointers, assessment IDs/reasons, watch details, parent IDs, owner
session hashes, operator/publisher identity and publication credentials. It
makes no DOI/publication claim and grants no source-reading or model-memory
capability.

All 1–64 original verified citation links remain visible. Replace the raw
`finding.evidence` and nested Ask evidence arrays with the public citation-ID
associations; they must never carry the original full quote strings.

For each citation, the displayed text is an exact prefix of its saved verified
quote, at most 280 Unicode codepoints. Set `excerpted` whenever the prefix is
shorter, expose the full quote's length/hash separately and display an explicit
excerpt label. Never insert an ellipsis into the quoted bytes. A fixed total
budget of 1,000 emitted quotation characters per paper applies across all
windows, aliases and repeated/overlapping quotes; every emitted character
counts. Use transitive paper aliases from the original pinned cited/read/index
identity context to group papers. Do not group by window/source ID or preferred
DOI alone, and do not expose uncited alias-context records.

When that paper's budget is exhausted, retain its citation metadata but set
`excerpt` to null and `excerpt_omission_reason` to `paper_limit`. The exact
preview shows those omissions. No claim or citation link disappears. The page
states that short saved excerpts establish quote integrity, not scientific
support, whole-paper coverage or a proof. Figures, table layout and mathematics
in extracted HTML remain limited.

### Bridge projection example

The values and hashes below are illustrative; implementation fixtures must
compute hashes from their exact synthetic source text.

```json
{
  "protocol": "polymath-public-finding-v1",
  "mode": "explore",
  "validation_scope": "model_assessment_and_saved_quote_integrity",
  "finding": {
    "kind": "bridge",
    "title": "Synthetic structural analogy",
    "field_left": "Fixture domain A",
    "field_right": "Fixture domain B",
    "explanation": "A model-proposed mapping between two synthetic examples.",
    "mappings": [{"left": "Redundant messages", "right": "Redundant pathways", "assumption": "The noise models are comparable."}],
    "open_question": "Would the mapping survive a real source audit?",
    "strongest_objection": "The examples may share only a superficial structure.",
    "objection_verdict": "The interpretation remains unverified.",
    "novelty_limitations": "Synthetic fixtures establish no novelty.",
    "citation_ids": ["citation-1", "citation-2"]
  },
  "papers": [
    {
      "id": "paper-1",
      "title": "Synthetic example A",
      "identifiers": {"arxiv_id": "2610.12345", "arxiv_version": 2},
      "links": [{"kind": "arxiv", "url": "https://arxiv.org/html/2610.12345v2"}]
    },
    {
      "id": "paper-2",
      "title": "Synthetic example B",
      "identifiers": {"doi": "10.1234/synthetic-b"},
      "links": [{"kind": "doi", "url": "https://doi.org/10.1234/synthetic-b"}]
    }
  ],
  "citations": [
    {
      "id": "citation-1",
      "paper_id": "paper-1",
      "side": "left",
      "claim_id": null,
      "supports": "A model interpretation of the synthetic structural comparison.",
      "source_kind": "full_text",
      "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "quote": {
        "excerpt": "Synthetic redundancy protects messages against noise.",
        "excerpted": false,
        "excerpt_start": 15,
        "excerpt_end": 68,
        "full_quote_start": 15,
        "full_quote_end": 68,
        "full_quote_chars": 53,
        "full_quote_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "excerpt_omission_reason": null
      },
      "coverage": {
        "snapshot_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "arxiv_id": "2610.12345",
        "arxiv_version": 2,
        "start": 8000,
        "end": 16000,
        "total": 42000
      }
    },
    {
      "id": "citation-2",
      "paper_id": "paper-2",
      "side": "right",
      "claim_id": null,
      "supports": "A separate paper supplies the other side of the comparison.",
      "source_kind": "abstract",
      "source_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
      "quote": {
        "excerpt": "Synthetic pathways carry fixture signals under noise.",
        "excerpted": false,
        "excerpt_start": 0,
        "excerpt_end": 53,
        "full_quote_start": 0,
        "full_quote_end": 53,
        "full_quote_chars": 53,
        "full_quote_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "excerpt_omission_reason": null
      }
    }
  ]
}
```

Offsets above are local to the saved source/window. For full text, add the
coverage `start` to obtain an extracted-snapshot offset. JavaScript readers must
interpret offsets as Unicode codepoints rather than UTF-16 code units.

### Ask-specific finding shape

An Ask projection uses the same `papers` and `citations` arrays. All claims are
present, and each original evidence item maps to a citation ID. For example:

```json
{
  "kind": "answer",
  "question": "What does the synthetic evidence establish?",
  "summary": "Only a bounded model assessment of synthetic examples.",
  "claims": [
    {
      "id": "claim-1",
      "text": "The proposed mechanism remains speculative.",
      "label": "speculative",
      "derivation": null,
      "caveats": ["No real source audit or scientific verification occurred."],
      "citation_ids": ["citation-1"]
    }
  ],
  "limitations": ["Synthetic examples are not scientific evidence."],
  "unanswered_questions": ["Does real evidence support the mechanism?"]
}
```

The associated citation has `side: "answer"` and `claim_id: "claim-1"`.

## API and DTO contracts

Owner routes use the current private response/error headers and browser
CSRF/Origin checks for mutations:

| Route | Request and result |
|---|---|
| `GET /api/v1/shares/capabilities` | `{sharing_available, reason}`; no session creation |
| `GET /api/v1/shares/preview?source_result_id=<private UUID>` | Owned current-supported positive finding → exact `SharePreview` |
| `POST /api/v1/shares` | `{source_result_id, expected_projection_sha256, idempotency_key}` → active `ShareRecord`; no separate approval |
| `GET /api/v1/shares?source_result_id=...&limit=20&before=...` | Owner's shares, including historical/revoked rows, with bounded pagination |
| `POST /api/v1/shares/{public_id}/revoke` | Idempotent permanent revocation; owned `ShareRecord` response |

`idempotency_key` is a canonical random UUID generated for this explicit Share
button action. A retry after a lost response reuses that same key and body.

`SharePreview` is:

```json
{
  "source_result_id": "00000000-0000-4000-8000-000000000001",
  "projection_sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
  "projection": {"protocol": "polymath-public-finding-v1", "mode": "explore", "validation_scope": "model_assessment_and_saved_quote_integrity", "finding": {}, "papers": [], "citations": []},
  "expires_at": "2026-11-04T12:00:00+00:00"
}
```

The empty projection members above stand for the complete validated DTO shown in
the preceding example. They are not valid production findings.

An owner `ShareRecord` contains:
`public_id`, `source_result_id` (private owner response only), `projection_sha256`,
`projection`, `created_at`, `expires_at`, `revoked_at`, `status`, `public_path`,
`idempotency_key` (owner-only). This list includes expired/revoked records so a
lost-response POST can be resolved without creating another grant.
`status` is `current`, `superseded`, `unsupported`, `revoked` or `expired`.
`public_path` is `/shared/<public_id>`, reconstructed from the application origin;
no caller-supplied host is accepted.

Public routes require no cookie, session bootstrap, CSRF token or model
availability:

| Route | Result |
|---|---|
| `GET /api/v1/public/findings/{public_id}` | Exact active public snapshot plus current badge |
| `GET /api/v1/public/library?q=...&limit=20&before=...` | Literal, bounded search over active current public projection text only |
| `GET /api/v1/public/map?q=...` | Bounded field/finding/paper map from that same public scope |

A public detail response is:

```json
{
  "public_id": "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA",
  "projection_sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
  "projection": {"protocol": "polymath-public-finding-v1", "mode": "explore", "validation_scope": "model_assessment_and_saved_quote_integrity", "finding": {}, "papers": [], "citations": []},
  "created_at": "2026-10-28T12:00:00+00:00",
  "expires_at": "2026-11-04T12:00:00+00:00",
  "status": "current"
}
```

Public responses never include the private result ID or other private binding
fields. List items can contain only `public_id`, `kind`, `mode`, `title`, a
bounded summary, public field names, `projection_sha256`, `created_at`,
`expires_at` and `status`; pagination cursors refer only to public IDs and identify the last visible item. A page filtered entirely by concurrent withdrawals ends safely; a fresh reload discovers the remaining current shares. Map IDs
use the public share ID, share-local paper IDs and hashes of explicitly public
field names. Map bounds match the existing viewer: 60 finding
candidates, 200 nodes and 500 edges, with explicit truncation/coverage fields.
Search never examines private goals, raw sources or unpublished findings.

The exported default policy disables new sharing until explicitly enabled in
server configuration. It is independent of Meta execution and publisher
credentials. A server-wide sharing kill switch may also hide public reads.

## Current state, lineage and revocation

A public projection stays byte-for-byte equivalent to its stored canonical
content. Derived status is separate:

| Private current state | Public direct-page badge |
|---|---|
| Same exact supported result, including unchanged assessments | `current` |
| Another result is current | `superseded` |
| This exact result is current but unsupported | `unsupported` |
| Share revoked/expired, owner expired or sharing disabled | Generic 404 |

A superseded page must not reveal whether an unshared successor is supported,
its text or any private assessment/revision ID, reason, count or timestamp. New
content never replaces the shared snapshot automatically. The default public
library/search/map includes only current supported active shares. The direct
owner-approved older page remains readable with its explicit warning until
expiry/revocation.

No parent or memory finding is shared recursively. This implementation
omits lineage edges entirely. If included later, both exact parent and child
must have independently active public shares, and the preview must include the
proposed public links. Private parents produce no dangling ID, title, count or
placeholder that reveals their existence. Parent expiry/revocation removes its
public edge immediately. A public page grants no `ResearchRunAccess`, source
body/window capability, reassessment right or permission to use another
owner's original evidence. A future follow-up/import needs its own explicit
public-projection binding and fresh evidence authorization.

Public HTML/JSON uses `Cache-Control: no-store`, `X-Content-Type-Options:
nosniff`, `Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex, nofollow`.
Render text without model HTML, Markdown links, images, embeds or automatic
third-party fetches. Source links use only server-reconstructed scholarly URLs
with `rel="noopener noreferrer"`. Missing, revoked and expired handles share the
same static 404 response. Revocation removes the share from every public query
surface; no public response returns revocation actor information. Copies already
saved by a viewer cannot be recalled, and the UI states that before sharing.

The public route must mount before the private `App`, which currently
bootstraps a browser session. Viewing `/shared/<public_id>` or the public
library/map must issue GET requests only, with no private workspace hydration,
SSE, automatic polling for jobs or calls to `/api/v1/session`.

## Required verification

Use synthetic findings, sources and quotations with real PostgreSQL. Cover:

- Exact preview/hash sharing, unknown-response idempotence, immutable projection
  and revocation history, ownership/CSRF/origin/expiry, current-source admission
  and source-head waits, losing assessment candidate denial, quotas and migration
  downgrade refusal.
- Complete Bridge and Ask text/labels/all citation associations, short Unicode
  prefixes, separate full/display offsets, original quote hash, aggregate caps
  across known aliases/windows/overlaps, source/window hashes and UUID-free
  public coverage. Unknown scholarly URLs and token-bearing URLs never appear.
- No private identifiers/goal/events/usage/source bodies/unused metadata in
  public detail/list/search/map; literal-query bounds; generic absent/revoked/
  expired 404; private route authorization unchanged; public source IDs cannot
  be used to read a private source.
- Changed/unsupported heads remove public discovery eligibility while direct
  historical pages retain immutable content and honest badges. Revocation and
  session expiry remove every public route, and a later share never revives the
  old public ID. Newer private findings remain private.
- A second unauthenticated browser can read the actual shared page/library/map,
  inspect cited snippets and scholarly links, reload without POST/session/model
  activity, and lose access after the owner revokes. Test mobile layout,
  accessibility, empty states, escaping and route isolation.

The milestone completes revocable opt-in public reading and discovery locally.
It does not claim a public deployment, permanent accounts, third-party source
redistribution, live scientific evaluation or native Meta/Zenodo verification.
