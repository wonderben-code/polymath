"""Mock Zenodo HTTP tests; no sandbox/production request or real DOI is made."""

import asyncio
import copy
import hashlib
import json
import logging

import httpx
import pytest
from pydantic import SecretStr

import polymath.zenodo as zenodo
from polymath.publication_contracts import (
    PublicationManifest,
    PublicationMetadata,
    publication_marker,
)
from polymath.zenodo import (
    MAX_FILE_BYTES,
    MAX_JSON_BYTES,
    RemoteFile,
    ZenodoClient,
    ZenodoDeposit,
    ZenodoError,
    canonical_remote_metadata,
    metadata_matches,
)

TOKEN = "synthetic-zenodo-token-never-in-urls"
ARTIFACT_ID = "a1111111-1111-4111-8111-111111111111"
ARTIFACT_RUN_ID = "b2222222-2222-4222-8222-222222222222"
BUCKET_ID = "c3333333-3333-4333-8333-333333333333"
CONTENT = b"# Explicit synthetic artifact\n\nNo scientific discovery is claimed.\n"
CONTENT_SHA256 = hashlib.sha256(CONTENT).hexdigest()
CONTENT_MD5 = hashlib.md5(CONTENT, usedforsecurity=False).hexdigest()


def manifest(destination="sandbox"):
    fields = {
        "protocol": "polymath-publication-v1",
        "artifact_id": ARTIFACT_ID,
        "artifact_run_id": ARTIFACT_RUN_ID,
        "markdown_sha256": CONTENT_SHA256,
        "markdown_bytes": len(CONTENT),
        "payload_sha256": "b" * 64,
        "basis_sha256": "c" * 64,
        "file_name": f"polymath-{ARTIFACT_ID}.md",
        "media_type": "text/markdown; charset=utf-8",
        "metadata": {
            "title": "Synthetic publication fixture",
            "description": "An explicitly synthetic transport test, not a scientific publication.",
            "creators": [{"name": "Synthetic Fixture Author", "affiliation": "Fixture only"}],
            "keywords": ["synthetic fixture"],
            "license": "cc-by-4.0",
            "upload_type": "publication",
            "publication_type": "preprint",
            "access_right": "open",
        },
        "publisher": {"identifier": "fixture-publisher", "display_name": "Fixture Publisher"},
        "destination": destination,
    }
    fields["metadata"] = PublicationMetadata.model_validate_json(
        json.dumps(fields["metadata"])
    ).model_dump(mode="json")
    return PublicationManifest.model_validate_json(
        json.dumps({**fields, "remote_marker": publication_marker(fields)})
    )


def origin(destination="sandbox"):
    return "https://sandbox.zenodo.org" if destination == "sandbox" else "https://zenodo.org"


def deposit_body(reviewed=None, *, identifier=123, published=False, files=False):
    reviewed = reviewed or manifest()
    host = origin(reviewed.destination)
    body = {
        "id": identifier,
        "submitted": published,
        "state": "done" if published else "unsubmitted",
        "metadata": canonical_remote_metadata(reviewed),
        "links": {
            "self": f"{host}/api/deposit/depositions/{identifier}",
            "bucket": f"{host}/api/files/{BUCKET_ID}",
        },
        "files": [
            {
                "filename": reviewed.file_name,
                "filesize": len(CONTENT),
                "checksum": "md5:" + CONTENT_MD5,
            }
        ]
        if files
        else [],
    }
    if published:
        body["metadata"]["doi"] = f"10.5072/zenodo.{identifier}"
        body["links"]["record_html"] = f"{host}/records/{identifier}"
    return body


def native_deposit(reviewed=None):
    reviewed = reviewed or manifest()
    return ZenodoDeposit(
        id=123,
        destination=reviewed.destination,
        submitted=False,
        state="unsubmitted",
        metadata=canonical_remote_metadata(reviewed),
        bucket_url=f"{origin(reviewed.destination)}/api/files/{BUCKET_ID}",
    )


def client(handler, *, destination="sandbox", enabled=True):
    return ZenodoClient(
        destination, SecretStr(TOKEN), live_enabled=enabled, transport=httpx.MockTransport(handler)
    )


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["sandbox", "production"])
async def test_mock_complete_deposit_upload_verified_bytes_and_publish(destination, caplog):
    reviewed = manifest(destination)
    calls = []
    remote_published = False

    def handler(request):
        nonlocal remote_published
        calls.append(request)
        assert str(request.url).startswith(origin(destination) + "/")
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert TOKEN not in str(request.url)
        if request.method == "POST" and request.url.path.endswith("/actions/publish"):
            remote_published = True
            return httpx.Response(202, json=deposit_body(reviewed, published=True, files=True))
        if request.method == "POST":
            assert json.loads(request.content) == {"metadata": canonical_remote_metadata(reviewed)}
            return httpx.Response(201, json=deposit_body(reviewed))
        if request.method == "PUT":
            assert request.content == CONTENT
            assert request.headers["content-type"] == "text/markdown; charset=utf-8"
            return httpx.Response(
                201,
                json={
                    "key": reviewed.file_name,
                    "size": len(CONTENT),
                    "checksum": "md5:" + CONTENT_MD5,
                },
            )
        if request.url.path.startswith("/api/files/"):
            return httpx.Response(200, content=CONTENT)
        return httpx.Response(
            200, json=deposit_body(reviewed, published=remote_published, files=True)
        )

    caplog.set_level(logging.INFO, logger="httpx")
    transport = client(handler, destination=destination)
    draft = await transport.create_draft(reviewed)
    assert not draft.published and draft.doi is None
    assert metadata_matches(draft, reviewed)
    uploaded = await transport.upload_file(draft, reviewed.file_name, CONTENT, CONTENT_SHA256)
    assert uploaded == RemoteFile(
        filename=reviewed.file_name, size=len(CONTENT), checksum="md5:" + CONTENT_MD5
    )
    read = await transport.get_file_bytes(draft, reviewed.file_name, maxbytes=len(CONTENT))
    assert hashlib.sha256(read).hexdigest() == reviewed.markdown_sha256
    reloaded = await transport.get_deposit(draft.id)
    assert reloaded.files == (uploaded,)
    published = await transport.publish(draft.id)
    assert published.published and published.destination == destination
    assert published.record_url == origin(destination) + "/records/123"
    assert published.doi == "10.5072/zenodo.123"
    assert published.doi_url == "https://doi.org/10.5072/zenodo.123"
    assert (await transport.get_deposit(draft.id)).published
    assert [request.method for request in calls] == ["POST", "PUT", "GET", "GET", "POST", "GET"]
    assert TOKEN not in caplog.text
    assert all("access_token" not in request.url.params for request in calls)


@pytest.mark.asyncio
async def test_secret_presence_does_not_enable_any_request():
    calls = []
    transport = client(lambda request: calls.append(request), enabled=False)
    operations = [
        lambda: transport.create_draft(manifest()),
        lambda: transport.get_deposit(123),
        lambda: transport.upload_file(
            native_deposit(), manifest().file_name, CONTENT, CONTENT_SHA256
        ),
        lambda: transport.get_file_bytes(native_deposit(), manifest().file_name),
        lambda: transport.publish(123),
        lambda: transport.find_deposits(manifest().remote_marker),
    ]
    for operation in operations:
        with pytest.raises(ZenodoError, match="zenodo_execution_disabled") as caught:
            await operation()
        assert not caught.value.outcome_unknown
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,unknown",
    [
        (401, "zenodo_access_denied", False),
        (403, "zenodo_access_denied", False),
        (404, "zenodo_deposit_not_found", False),
        (409, "zenodo_state_conflict", False),
        (422, "zenodo_request_rejected", False),
        (429, "zenodo_rate_limited", False),
        (503, "zenodo_request_failed", True),
        (302, "zenodo_request_failed", True),
    ],
)
async def test_mutation_http_failures_are_static_bounded_and_never_retried(status, code, unknown):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            content=("private server body " + TOKEN).encode(),
            headers={"Location": "https://attacker.invalid/steal", "Retry-After": "1"},
        )

    with pytest.raises(ZenodoError) as caught:
        await client(handler).create_draft(manifest())
    assert caught.value.code == code and caught.value.outcome_unknown is unknown
    assert TOKEN not in str(caught.value) and "private server body" not in repr(caught.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [True, False])
@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
async def test_network_errors_retain_mutation_uncertainty_without_provider_details(error, mutation):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("private transport detail " + TOKEN)

    transport = client(handler)
    with pytest.raises(ZenodoError) as caught:
        if mutation:
            await transport.create_draft(manifest())
        else:
            await transport.get_deposit(123)
    assert caught.value.outcome_unknown is mutation
    assert caught.value.code in ("zenodo_timeout", "zenodo_transport_error")
    assert TOKEN not in repr(caught.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_wall_clock_timeout_bounds_a_stalled_response(monkeypatch):
    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()
            yield b"unreachable"

    monkeypatch.setattr(zenodo, "REQUEST_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ZenodoError, match="zenodo_timeout") as caught:
        await client(lambda request: httpx.Response(201, stream=Stalled())).create_draft(manifest())
    assert caught.value.outcome_unknown


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bucket",
    [
        f"http://sandbox.zenodo.org/api/files/{BUCKET_ID}",
        f"https://zenodo.org/api/files/{BUCKET_ID}",
        f"https://sandbox.zenodo.org.attacker.invalid/api/files/{BUCKET_ID}",
        f"https://user:password@sandbox.zenodo.org/api/files/{BUCKET_ID}",
        f"https://sandbox.zenodo.org:443/api/files/{BUCKET_ID}",
        f"https://127.0.0.1/api/files/{BUCKET_ID}",
        f"https://sandbox.zenodo.org/api/files/{BUCKET_ID}?token=private",
        f"https://sandbox.zenodo.org/api/files/{BUCKET_ID}#fragment",
        f"https://sandbox.zenodo.org/api/files/%2e%2e/{BUCKET_ID}",
        f"https://sandbox.zenodo.org/api/files/{BUCKET_ID}/../other",
        f"https://sandbox.zenodo.org/api/files/{BUCKET_ID}/",
        f"https://sandbox.zenodo.org/api/files/{BUCKET_ID}\\other",
    ],
)
async def test_untrusted_bucket_links_never_become_credentialed_requests(bucket):
    calls = []
    response = deposit_body()
    response["links"]["bucket"] = bucket

    def handler(request):
        calls.append(request)
        return httpx.Response(201, json=response)

    with pytest.raises(ZenodoError, match="zenodo_invalid_response") as caught:
        await client(handler).create_draft(manifest())
    assert caught.value.outcome_unknown and len(calls) == 1
    assert calls[0].url.host == "sandbox.zenodo.org"
    tampered = native_deposit().model_copy(update={"bucket_url": bucket})
    with pytest.raises(ZenodoError, match="invalid_zenodo_deposit"):
        await client(handler).get_file_bytes(tampered, manifest().file_name)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename",
    [
        "../escape.md",
        "/absolute.md",
        "file%2fescape.md",
        "x?token=a",
        "x#y",
        "x\\y",
        "a\n.md",
        "x" * 61,
    ],
)
async def test_invalid_file_names_are_rejected_before_network(filename):
    calls = []
    transport = client(lambda request: calls.append(request))
    with pytest.raises(ZenodoError, match="invalid_publication_file_name"):
        await transport.upload_file(native_deposit(), filename, CONTENT, CONTENT_SHA256)
    with pytest.raises(ZenodoError, match="invalid_publication_file_name"):
        await transport.get_file_bytes(native_deposit(), filename)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, True, "123", 2**63])
async def test_deposit_identifiers_cannot_change_the_endpoint_path(value):
    calls = []
    transport = client(lambda request: calls.append(request))
    with pytest.raises(ZenodoError, match="invalid_zenodo_deposit_id"):
        await transport.get_deposit(value)
    with pytest.raises(ZenodoError, match="invalid_zenodo_deposit_id"):
        await transport.publish(value)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["title", "marker", "extra_metadata", "submitted", "bucket_missing"]
)
async def test_create_response_must_preserve_exact_reviewed_metadata_and_draft_state(change):
    response = deposit_body()
    if change == "title":
        response["metadata"]["title"] = "Different remote text"
    elif change == "marker":
        response["metadata"]["keywords"] = ["synthetic fixture"]
    elif change == "extra_metadata":
        response["metadata"]["related_identifiers"] = [{"identifier": "unapproved"}]
    elif change == "submitted":
        response["submitted"] = True
        response["state"] = "done"
    else:
        response["links"].pop("bucket")
    with pytest.raises(ZenodoError) as caught:
        await client(lambda request: httpx.Response(201, json=response)).create_draft(manifest())
    assert caught.value.outcome_unknown
    assert caught.value.code in (
        "zenodo_metadata_mismatch",
        "zenodo_unexpected_state",
        "zenodo_invalid_response",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["missing_doi", "reserved_only", "missing_record", "in_progress", "not_submitted"]
)
async def test_publish_response_never_invents_a_doi_or_completed_publication(change):
    response = deposit_body(published=True)
    if change == "missing_doi":
        response["metadata"].pop("doi")
    elif change == "reserved_only":
        response["metadata"].pop("doi")
        response["metadata"]["prereserve_doi"] = {"doi": "10.5072/zenodo.123", "recid": 123}
    elif change == "missing_record":
        response["links"].pop("record_html")
    elif change == "in_progress":
        response["state"] = "inprogress"
    else:
        response["submitted"] = False
    result = await client(lambda request: httpx.Response(202, json=response)).publish(123)
    assert not result.published
    if change in ("missing_doi", "reserved_only"):
        assert result.doi is None and result.doi_url is None


@pytest.mark.asyncio
async def test_documented_inprogress_unsubmitted_draft_accepts_file_upload():
    reviewed = manifest()
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "POST":
            body = deposit_body()
            body["state"] = "inprogress"
            return httpx.Response(201, json=body)
        return httpx.Response(
            201,
            json={"key": reviewed.file_name, "size": len(CONTENT), "checksum": CONTENT_MD5},
        )

    transport = client(handler)
    draft = await transport.create_draft(reviewed)
    assert draft.state == "inprogress" and not draft.submitted
    uploaded = await transport.upload_file(draft, reviewed.file_name, CONTENT, CONTENT_SHA256)
    assert uploaded.size == len(CONTENT)
    assert [request.method for request in calls] == ["POST", "PUT"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_path", [False, True])
async def test_actual_record_identity_is_read_from_documented_fields_or_returned_legacy_path(
    legacy_path,
):
    body = deposit_body(published=True)
    body["metadata"].pop("doi")
    body["doi"] = "10.5072/zenodo.456"
    body["doi_url"] = "https://doi.org/10.5072/zenodo.456"
    if legacy_path:
        body["links"]["record_html"] = origin() + "/record/456"
    else:
        body["links"].pop("record_html")
        body["record_id"] = 456
        body["record_url"] = origin() + "/records/456"
    parsed = await client(lambda request: httpx.Response(200, json=body)).get_deposit(123)
    assert parsed.id == 123 and parsed.record_id == 456
    assert parsed.record_url == origin() + ("/record/456" if legacy_path else "/records/456")
    assert parsed.doi == "10.5072/zenodo.456" and parsed.published
    assert metadata_matches(parsed, manifest())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["conflicting_record", "foreign_record", "record_id_type", "doi_url", "doi_mismatch"]
)
async def test_documented_top_level_identifiers_and_links_cannot_contradict_each_other(problem):
    body = deposit_body(published=True)
    body["record_id"] = 123
    body["record_url"] = origin() + "/records/123"
    body["doi"] = body["metadata"]["doi"]
    body["doi_url"] = "https://doi.org/" + body["doi"]
    if problem == "conflicting_record":
        body["record_url"] = origin() + "/records/456"
    elif problem == "foreign_record":
        body["record_url"] = "https://attacker.invalid/records/123"
    elif problem == "record_id_type":
        body["record_id"] = True
    elif problem == "doi_url":
        body["doi_url"] = "https://attacker.invalid/10.5072/zenodo.123"
    else:
        body["doi"] = "10.5072/zenodo.456"
    with pytest.raises(ZenodoError, match="zenodo_invalid_response"):
        await client(lambda request: httpx.Response(200, json=body)).get_deposit(123)


@pytest.mark.asyncio
async def test_documented_legacy_file_size_normalizes_without_losing_exact_byte_length():
    body = deposit_body(files=True)
    body["files"][0]["filesize"] = str(len(CONTENT))
    parsed = await client(lambda request: httpx.Response(200, json=body)).get_deposit(123)
    assert parsed.files[0].size == len(CONTENT)
    body["files"][0]["filesize"] = "+" + str(len(CONTENT))
    with pytest.raises(ZenodoError, match="zenodo_invalid_response"):
        await client(lambda request: httpx.Response(200, json=body)).get_deposit(123)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "doi_url",
        "foreign_record",
        "record_creds",
        "different_record",
        "id",
        "bool_submitted",
        "creator_shape",
        "malformed_reserved",
        "bad_date",
    ],
)
async def test_remote_identity_metadata_and_followed_record_links_are_strict(change):
    response = deposit_body(published=True)
    if change == "doi_url":
        response["metadata"]["doi"] = "https://doi.org/10.5072/zenodo.123"
    elif change == "foreign_record":
        response["links"]["record_html"] = "https://attacker.invalid/records/123"
    elif change == "record_creds":
        response["links"]["record_html"] = "https://secret@sandbox.zenodo.org/records/123"
    elif change == "different_record":
        response["record_id"] = 123
        response["links"]["record_html"] = origin() + "/records/456"
    elif change == "id":
        response["id"] = 456
    elif change == "bool_submitted":
        response["submitted"] = "true"
    elif change == "creator_shape":
        response["metadata"]["creators"][0]["url"] = "https://attacker.invalid/author"
    elif change == "malformed_reserved":
        response["metadata"]["prereserve_doi"] = {"doi": "javascript:unsafe"}
    else:
        response["metadata"]["publication_date"] = "2026-02-31"
    with pytest.raises(ZenodoError, match="zenodo_invalid_response") as caught:
        await client(lambda request: httpx.Response(200, json=response)).get_deposit(123)
    assert not caught.value.outcome_unknown


@pytest.mark.asyncio
@pytest.mark.parametrize("checksum", [None, "md5", "bare_md5", "sha256"])
async def test_upload_response_checksum_is_bounded_metadata_not_remote_sha_proof(checksum):
    value = {
        None: None,
        "md5": "md5:" + CONTENT_MD5,
        "bare_md5": CONTENT_MD5,
        "sha256": "sha256:" + CONTENT_SHA256,
    }[checksum]

    def handler(request):
        if request.method == "PUT":
            return httpx.Response(
                201, json={"key": manifest().file_name, "size": len(CONTENT), "checksum": value}
            )
        return httpx.Response(200, content=b"Different remote bytes")

    transport = client(handler)
    uploaded = await transport.upload_file(
        native_deposit(), manifest().file_name, CONTENT, CONTENT_SHA256
    )
    assert uploaded.checksum == value
    read = await transport.get_file_bytes(native_deposit(), manifest().file_name)
    assert hashlib.sha256(read).hexdigest() != CONTENT_SHA256
    assert "sha256" not in uploaded.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["filename", "size", "checksum", "invalid_checksum"])
async def test_upload_mismatches_retain_uncertainty_about_remote_bytes(change):
    body = {"key": manifest().file_name, "size": len(CONTENT), "checksum": "md5:" + CONTENT_MD5}
    body[change if change != "filename" else "key"] = {
        "filename": "different.md",
        "size": len(CONTENT) + 1,
        "checksum": "md5:" + "0" * 32,
        "invalid_checksum": "provider arbitrary text",
    }[change]
    if change == "invalid_checksum":
        body["checksum"] = body.pop("invalid_checksum")
    with pytest.raises(ZenodoError) as caught:
        await client(lambda request: httpx.Response(201, json=body)).upload_file(
            native_deposit(), manifest().file_name, CONTENT, CONTENT_SHA256
        )
    assert caught.value.outcome_unknown
    assert caught.value.code in ("zenodo_file_mismatch", "zenodo_invalid_response")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,digest",
    [
        (b"", CONTENT_SHA256),
        (CONTENT, "0" * 64),
        (b"\xff", hashlib.sha256(b"\xff").hexdigest()),
        (b"x" * (MAX_FILE_BYTES + 1), "0" * 64),
    ],
    ids=["empty", "wrong_hash", "invalid_utf8", "too_large"],
)
async def test_invalid_or_wrong_version_local_file_is_never_sent(content, digest):
    calls = []
    with pytest.raises(ZenodoError, match="invalid_publication_file"):
        await client(lambda request: calls.append(request)).upload_file(
            native_deposit(), manifest().file_name, content, digest
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [True, False])
@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"{}not-json",
        b'{"id":123,"id":456}',
        b'{"id":NaN}',
        b'{"id":Infinity}',
        b'{"id":"bad\\u0000value"}',
    ],
)
async def test_json_is_strict_utf8_finite_unambiguous_and_public_text(payload, mutation):
    transport = client(lambda request: httpx.Response(201 if mutation else 200, content=payload))
    with pytest.raises(ZenodoError, match="zenodo_invalid_response") as caught:
        if mutation:
            await transport.create_draft(manifest())
        else:
            await transport.get_deposit(123)
    assert caught.value.outcome_unknown is mutation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,parts,code",
    [
        ({"Content-Length": str(MAX_JSON_BYTES + 1)}, (b"{}",), "zenodo_response_too_large"),
        ({}, (b"x" * MAX_JSON_BYTES, b"x"), "zenodo_response_too_large"),
        ({"Content-Length": "-1"}, (b"{}",), "zenodo_invalid_response"),
        ({"Content-Length": "not-a-size"}, (b"{}",), "zenodo_invalid_response"),
        ({"Content-Encoding": "gzip"}, (b"not-gzip",), "zenodo_invalid_response"),
    ],
)
async def test_streamed_response_limits_and_encoding_are_enforced(headers, parts, code):
    with pytest.raises(ZenodoError, match=code) as caught:
        await client(
            lambda request: httpx.Response(201, stream=Chunks(*parts), headers=headers)
        ).create_draft(manifest())
    assert caught.value.outcome_unknown


@pytest.mark.asyncio
async def test_get_actual_file_bytes_uses_its_own_bound_and_never_follows_redirects():
    transport = client(lambda request: httpx.Response(200, stream=Chunks(b"123", b"456")))
    with pytest.raises(ZenodoError, match="zenodo_response_too_large") as caught:
        await transport.get_file_bytes(native_deposit(), manifest().file_name, maxbytes=5)
    assert not caught.value.outcome_unknown
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.invalid/private"})

    with pytest.raises(ZenodoError, match="zenodo_request_failed"):
        await client(redirect).get_file_bytes(native_deposit(), manifest().file_name)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_marker_lookup_verifies_exact_keywords_and_keeps_duplicate_candidates_visible():
    reviewed = manifest()
    first = deposit_body(identifier=123)
    second = deposit_body(identifier=124)
    false_positive = deposit_body(identifier=125)
    false_positive["metadata"]["keywords"] = ["prefix " + reviewed.remote_marker]
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/deposit/depositions"
        assert request.url.params["q"] == f'keywords:"{reviewed.remote_marker}"'
        assert request.url.params["size"] == "10"
        assert request.url.params["page"] == "1"
        return httpx.Response(200, json=[first, false_positive, second])

    found = await client(handler).find_deposits(reviewed.remote_marker)
    assert [deposit.id for deposit in found] == [123, 124]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_lookup_scans_two_pages_and_never_mistakes_a_full_cap_for_absence():
    reviewed = manifest()
    calls = []
    unrelated = {"metadata": {"keywords": ["some other item"]}}

    def complete(request):
        calls.append(request)
        return httpx.Response(
            200,
            json=[copy.deepcopy(unrelated) for _ in range(10)]
            if len(calls) == 1
            else [deposit_body()],
        )

    found = await client(complete).find_deposits(reviewed.remote_marker)
    assert len(found) == 1 and len(calls) == 2
    assert calls[-1].url.params["page"] == "2"
    calls.clear()

    def truncated(request):
        calls.append(request)
        return httpx.Response(
            200, json=[deposit_body()] + [copy.deepcopy(unrelated) for _ in range(9)]
        )

    with pytest.raises(ZenodoError, match="zenodo_lookup_truncated") as caught:
        await client(truncated).find_deposits(reviewed.remote_marker)
    assert not caught.value.outcome_unknown
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_duplicate_remote_id_with_conflicting_records_cannot_be_reconciled_as_unique():
    first, changed = deposit_body(), deposit_body()
    changed["metadata"]["title"] = "Conflicting remote metadata"
    with pytest.raises(ZenodoError, match="zenodo_lookup_conflict"):
        await client(lambda request: httpx.Response(200, json=[first, changed])).find_deposits(
            manifest().remote_marker
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [None, {}, [{"metadata": {"keywords": "not a list"}}], [{"metadata": {"keywords": [123]}}]],
)
async def test_malformed_lookup_cannot_prove_an_empty_or_unique_result(response):
    with pytest.raises(ZenodoError, match="zenodo_invalid_response"):
        await client(lambda request: httpx.Response(200, json=response)).find_deposits(
            manifest().remote_marker
        )


@pytest.mark.asyncio
async def test_destination_marker_and_request_file_limit_preflight():
    calls = []
    transport = client(lambda request: calls.append(request))
    with pytest.raises(ZenodoError, match="zenodo_destination_mismatch"):
        await transport.create_draft(manifest("production"))
    with pytest.raises(ZenodoError, match="invalid_zenodo_deposit"):
        await transport.get_file_bytes(native_deposit(manifest("production")), manifest().file_name)
    for marker in ("not-a-marker", '" OR anything', "polymath-" + "A" * 64):
        with pytest.raises(ZenodoError, match="invalid_publication_marker"):
            await transport.find_deposits(marker)
    for limit in (0, MAX_FILE_BYTES + 1, True):
        with pytest.raises(ZenodoError, match="invalid_publication_file_limit"):
            await transport.get_file_bytes(native_deposit(), manifest().file_name, maxbytes=limit)
    assert calls == []


@pytest.mark.parametrize(
    "destination,token,enabled",
    [
        ("other", SecretStr(TOKEN), False),
        ("sandbox", TOKEN, False),
        ("sandbox", SecretStr(""), False),
        ("sandbox", SecretStr("bad\nsecret"), False),
        ("sandbox", SecretStr(TOKEN), 1),
    ],
)
def test_client_requires_explicit_known_destination_and_safe_secret_transport(
    destination, token, enabled
):
    with pytest.raises(ValueError):
        ZenodoClient(destination, token, live_enabled=enabled)


def test_canonical_metadata_is_exact_reviewed_data_and_marker_without_callback_fields():
    reviewed = manifest()
    metadata = canonical_remote_metadata(reviewed)
    assert metadata == {
        **reviewed.metadata.model_dump(mode="json", exclude_none=True),
        "keywords": ["synthetic fixture", reviewed.remote_marker],
    }
    assert metadata_matches(native_deposit(), reviewed)
    tampered = native_deposit().model_copy(
        update={"metadata": {**metadata, "description": "Changed"}}
    )
    assert not metadata_matches(tampered, reviewed)
    broken = reviewed.model_copy(update={"remote_marker": "polymath-" + "0" * 64})
    with pytest.raises(ZenodoError, match="invalid_publication_manifest"):
        canonical_remote_metadata(broken)
