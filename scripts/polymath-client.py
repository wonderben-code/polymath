#!/usr/bin/env python3
"""Standalone standard-library client for Polymath's versioned HTTP API.

No command runs by default. Public reads never load cookies or initialize an
owner session. Model execution remains entirely subject to server configuration.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import http.cookiejar
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import stat
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

COOKIE_NAME = "polymath_session"
MAX_JSON_BYTES = 20 * 1024 * 1024
MAX_EXPORT_BYTES = 262_144
MAX_COOKIE_BYTES = 16_384
MAX_EVENT_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 20 * 1024 * 1024
MAX_CURSOR = 2**63 - 1
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
DIGEST = re.compile(r"[a-f0-9]{64}")
ASK_OVERVIEW_PROTOCOL = "polymath-ask-claim-overview-v1"
ARTIFACT_CONTEXT_PROTOCOL = "polymath-artifact-scientific-context-v1"
ASSESSMENT_OVERVIEW_PROTOCOL = "polymath-ask-reassessment-overview-v1"
EXPLORE_COLLECTION_PROTOCOL = "polymath-explore-collection-v1"
SCIENTIFIC_CAPABILITIES = (
    "artifact_scientific_context_v1",
    "ask_claim_overview_v1",
    "ask_reassessment_overview_v1",
)
PUBLIC_ACCESS_REASONS = frozenset(
    {
        "public_research_disabled",
        "public_research_activation_required",
        "public_research_authority_expired",
        "public_research_stopped",
        "public_research_frozen",
        "public_research_budget_exhausted",
        "public_research_daily_budget_exhausted",
        "public_research_session_budget_exhausted",
        "public_research_session_daily_budget_exhausted",
        "public_research_prior_unresolved",
        "public_research_resource_unavailable",
        "operator_required",
        "research_unavailable",
    }
)
SAFE_ERRORS = {
    "research_session_required",
    "csrf_check_failed",
    "origin_check_failed",
    "research_unavailable",
    "research_run_not_found",
    "research_result_not_available",
    "research_basis_not_found",
    "invalid_research_request",
    "invalid_event_cursor",
    "active_run_limit",
    "research_queue_full",
    "research_storage_unavailable",
    "artifact_not_found",
    "artifact_source_not_found",
    "artifact_storage_unavailable",
    "sharing_not_found",
    "sharing_unavailable",
    "sharing_storage_unavailable",
    "sharing_invalid_request",
    "discovery_not_found",
    "discovery_storage_unavailable",
    "invalid_discovery_request",
    "public_request_rate_limited",
    "public_resource_controls_unavailable",
    "ask_reassessment_overview_required",
    "meta_research_scientific_protocol_unavailable",
    "scientific_context_not_available",
    "scientific_context_integrity_failed",
    "scientific_context_admission_closed",
    "scientific_context_admission_changed",
    "ask_overview_wrong_run",
    "artifact_context_too_large",
    "meta_research_explore_protocol_unavailable",
    "explore_collection_not_found",
    "explore_collection_storage_unavailable",
    "invalid_explore_collection_request",
}
SAFE_ERRORS.update(PUBLIC_ACCESS_REASONS)
SECRET_FIELDS = {
    "csrf_token",
    "cookie",
    "set-cookie",
    "authorization",
    "token",
    "api_key",
    "password",
    "secret",
}


class ClientError(Exception):
    """A static code and optional HTTP status; never an underlying exception."""

    def __init__(self, code, status=None, retry_after_seconds=None):
        super().__init__(code)
        self.code, self.status = code, status
        self.retry_after_seconds = retry_after_seconds


def canonical_origin(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ClientError("invalid_base_url")
    if any(ord(character) < 33 or ord(character) == 127 for character in value) or "\\" in value:
        raise ClientError("invalid_base_url")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or "%" in parsed.netloc
        ):
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError
        if parsed.netloc.endswith(":"):
            raise ValueError
        if parsed.netloc.startswith("["):
            ipaddress.IPv6Address(host)
        if ":" not in host and (
            not re.fullmatch(r"[a-z0-9.-]+", host)
            or any(
                not part or part.startswith("-") or part.endswith("-") for part in host.split(".")
            )
        ):
            raise ValueError
        if parsed.scheme == "http" and host != "localhost":
            try:
                if not ipaddress.ip_address(host).is_loopback:
                    raise ValueError
            except ValueError:
                raise ClientError("invalid_base_url") from None
        authority = f"[{host}]" if ":" in host else host
        if port != (443 if parsed.scheme == "https" else 80):
            authority += f":{port}"
        return f"{parsed.scheme}://{authority}"
    except (ValueError, UnicodeError):
        raise ClientError("invalid_base_url") from None


def canonical_uuid(value):
    try:
        text = str(value)
        if str(uuid.UUID(text)) != text:
            raise ValueError
        return text
    except (ValueError, TypeError, AttributeError):
        raise ClientError("invalid_identifier") from None


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ClientError("invalid_arguments")
    return value


def _duration(value, minimum=0.001, maximum=60):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ClientError("invalid_arguments")
    return float(value)


def _query(value):
    if not isinstance(value, str) or len(value) > 200 or any(ord(char) < 32 for char in value):
        raise ClientError("invalid_arguments")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ClientError("invalid_arguments") from None
    return value


def _research_access(value):
    try:
        if not isinstance(value, dict) or set(value) != {
            "protocol",
            "audience",
            "available",
            "reason",
            "expected_activation_sha256",
            "expires_at",
            "limits",
            "remaining_session_allowance_microdollars",
            "remaining_session_jobs_today",
            "retry_after_seconds",
        }:
            raise ValueError
        audience, available, reason = (
            value["audience"],
            value["available"],
            value["reason"],
        )
        if (
            value["protocol"] != "polymath-research-access-v1"
            or audience not in (None, "public_sessions", "operator_browser")
            or type(available) is not bool
            or (reason is not None and reason not in PUBLIC_ACCESS_REASONS)
            or available != (reason is None)
        ):
            raise ValueError
        if value["retry_after_seconds"] is not None:
            _integer(value["retry_after_seconds"], 0, 86400)
        metadata = [
            value[key]
            for key in (
                "expected_activation_sha256",
                "expires_at",
                "limits",
            )
        ]
        counters = (
            value["remaining_session_allowance_microdollars"],
            value["remaining_session_jobs_today"],
        )
        if audience != "public_sessions":
            if available or any(item is not None for item in (*metadata, *counters)):
                raise ValueError
            if audience == "operator_browser" and reason != "operator_required":
                raise ValueError
            return value
        if all(item is None for item in metadata) and not available:
            if any(item is not None for item in counters):
                raise ValueError
            return value
        if any(item is None for item in metadata):
            raise ValueError
        if available and any(item is None for item in counters):
            raise ValueError
        digest, expiry = value["expected_activation_sha256"], value["expires_at"]
        if (
            not isinstance(digest, str)
            or not DIGEST.fullmatch(digest)
            or not isinstance(expiry, str)
            or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)",
                expiry,
            )
            or datetime.fromisoformat(expiry).utcoffset().total_seconds() != 0
        ):
            raise ValueError
        limits = value["limits"]
        if not isinstance(limits, dict) or set(limits) != {
            "session_total_cap_microdollars",
            "session_daily_cap_microdollars",
            "max_jobs_per_session_day",
            "max_active_jobs_per_session",
            "task_kinds",
            "discovery_intent",
        }:
            raise ValueError
        total, daily, remaining = (
            limits["session_total_cap_microdollars"],
            limits["session_daily_cap_microdollars"],
            value["remaining_session_allowance_microdollars"],
        )
        for amount in (total, daily, *((remaining,) if remaining is not None else ())):
            if not isinstance(amount, str) or not re.fullmatch(r"(?:0|[1-9][0-9]{0,8})", amount):
                raise ValueError
        if not 1 <= int(daily) <= int(total) <= 150_000_000 or (
            remaining is not None and int(remaining) > int(total)
        ):
            raise ValueError
        _integer(limits["max_jobs_per_session_day"], 1, 12)
        _integer(limits["max_active_jobs_per_session"], 1, 1)
        if value["remaining_session_jobs_today"] is not None:
            _integer(
                value["remaining_session_jobs_today"],
                0,
                limits["max_jobs_per_session_day"],
            )
        tasks = limits["task_kinds"]
        if (
            not isinstance(tasks, list)
            or len(tasks) != 5
            or set(tasks)
            != {
                "research",
                "formalise",
                "write_summary",
                "write_paper",
                "reassess_finding",
            }
        ):
            raise ValueError
        if limits["discovery_intent"] is not None:
            _discovery_intent(limits["discovery_intent"])
        if available and int(remaining) == 0:
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, AttributeError, ClientError):
        raise ClientError("invalid_research_access_response") from None


def _discovery_intent(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"protocol", "max_stages", "max_depth"}
        or value["protocol"] != "polymath-surprise-ladder-v1"
    ):
        raise ClientError("invalid_arguments")
    return {
        "protocol": value["protocol"],
        "max_stages": _integer(value["max_stages"], 1, 4),
        "max_depth": _integer(value["max_depth"], 1, 3),
    }


def _scientific_intent(value, protocol):
    if not isinstance(value, dict) or value != {"protocol": protocol}:
        raise ClientError("invalid_arguments")
    return {"protocol": protocol}


def _explore_collection_intent(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"protocol", "max_bridges"}
        or value["protocol"] != EXPLORE_COLLECTION_PROTOCOL
    ):
        raise ClientError("invalid_arguments")
    return {
        "protocol": EXPLORE_COLLECTION_PROTOCOL,
        "max_bridges": _integer(value["max_bridges"], 1, 4),
    }


def _explore_protocols(value):
    try:
        if (
            not isinstance(value, dict)
            or set(value) != {"protocol", "activation_sha256", "explore_collection_intent"}
            or value["protocol"] != "polymath-explore-collection-access-v1"
        ):
            raise ValueError
        digest, intent = value["activation_sha256"], value["explore_collection_intent"]
        if digest is not None and (not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
            raise ValueError
        if intent is not None:
            if digest is None:
                raise ValueError
            _explore_collection_intent(intent)
        return value
    except (ValueError, TypeError, KeyError, AttributeError, ClientError):
        raise ClientError("invalid_explore_protocols_response") from None


def _research_protocols(value):
    try:
        if not isinstance(value, dict) or set(value) != {
            "protocol",
            "activation_sha256",
            "capabilities",
        }:
            raise ValueError
        digest, capabilities = value["activation_sha256"], value["capabilities"]
        if (
            value["protocol"] != "polymath-research-protocols-v1"
            or digest is not None
            and (not isinstance(digest, str) or not DIGEST.fullmatch(digest))
            or not isinstance(capabilities, list)
            or capabilities != [item for item in SCIENTIFIC_CAPABILITIES if item in capabilities]
            or digest is None
            and capabilities
        ):
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ClientError("invalid_research_protocols_response") from None


def _answer_overview(value, *, finding_key="finding", public=False):
    """Check supplied overview integrity; scientific support remains server-verified."""
    if not isinstance(value, dict) or "overview_binding" not in value:
        return value
    try:
        binding, finding = value["overview_binding"], value[finding_key]
        if (
            not isinstance(binding, dict)
            or set(binding) != {"protocol", "claim_ids"}
            or binding["protocol"] != ASK_OVERVIEW_PROTOCOL
            or not isinstance(finding, dict)
            or finding.get("kind" if public else "outcome") != "answer"
            or "mode" in value
            and value["mode"] != "ask"
            or "status" in value
            and value["status"] != "answer"
        ):
            raise ValueError
        ids, claims = binding["claim_ids"], finding.get("claims")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 8
            or not all(
                isinstance(item, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", item)
                for item in ids
            )
            or len(set(ids)) != len(ids)
            or not isinstance(claims, list)
            or not 1 <= len(claims) <= 8
        ):
            raise ValueError
        by_id = {}
        for ordinal, claim in enumerate(claims, 1):
            evidence_key = "citation_ids" if public else "evidence"
            if (
                not isinstance(claim, dict)
                or not isinstance(claim.get("id"), str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", claim["id"])
                or public
                and claim["id"] != f"claim-{ordinal}"
                or claim["id"] in by_id
                or not isinstance(claim.get("text"), str)
                or not 1 <= len(claim["text"]) <= 2000
                or not claim["text"].strip()
                or claim.get("label") not in ("known", "postulated", "speculative")
                or not isinstance(claim.get(evidence_key), list)
                or not 1 <= len(claim[evidence_key]) <= 8
                or claim["label"] == "postulated"
                and (
                    not isinstance(claim.get("derivation"), str) or not claim["derivation"].strip()
                )
                or claim["label"] == "speculative"
                and not claim.get("caveats")
            ):
                raise ValueError
            citations = claim[evidence_key]
            if public:
                if any(
                    not isinstance(item, str) or not re.fullmatch(r"citation-[1-9][0-9]?", item)
                    for item in citations
                ) or len(set(citations)) != len(citations):
                    raise ValueError
            elif any(
                not isinstance(item, dict)
                or set(item) != {"source_id", "quote", "supports"}
                or any(not isinstance(item[key], str) or not item[key].strip() for key in item)
                for item in citations
            ):
                raise ValueError
            by_id[claim["id"]] = claim
        if any(item not in by_id for item in ids):
            raise ValueError
        summary = "\n\n".join(by_id[item]["text"] for item in ids)
        summary.encode("utf-8")
        if len(summary) > 3000 or finding.get("summary") != summary:
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ClientError("invalid_overview_response") from None


def _public_scientific_metadata_unchecked(value):
    def private_context(item):
        if isinstance(item, dict):
            if set(item) & {
                "scientific_context",
                "scientific_context_sha256",
                "ancestry_nodes",
                "ancestry_edges",
                "applied_assessments",
                "source_revision",
            }:
                raise ClientError("invalid_public_scientific_response")
            for child in item.values():
                private_context(child)
        elif isinstance(item, list):
            for child in item:
                private_context(child)

    private_context(value)
    projection = value.get("projection") if isinstance(value, dict) else None
    if isinstance(projection, dict) and "overview_binding" in projection:
        _answer_overview(projection, public=True)
        citations = projection.get("citations")
        if not isinstance(citations, list) or not 1 <= len(citations) <= 64:
            raise ClientError("invalid_public_scientific_response")
        citation_ids = set()
        for ordinal, item in enumerate(citations, 1):
            if not isinstance(item, dict) or item.get("id") != f"citation-{ordinal}":
                raise ClientError("invalid_public_scientific_response")
            citation_ids.add(item["id"])
        if any(
            not set(claim["citation_ids"]) <= citation_ids
            for claim in projection["finding"]["claims"]
        ):
            raise ClientError("invalid_public_scientific_response")
        encoded = json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != value.get("projection_sha256"):
            raise ClientError("invalid_public_scientific_response")
    return value


def _public_scientific_metadata(value):
    try:
        return _public_scientific_metadata_unchecked(value)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise ClientError("invalid_public_scientific_response") from None


def _scientific_history(value):
    """Validate only present protocol extensions; legacy response bodies pass through."""
    if not isinstance(value, dict):
        return value
    try:
        fields = (
            ("ask_overview_intent", ASK_OVERVIEW_PROTOCOL),
            ("assessment_overview_intent", ASSESSMENT_OVERVIEW_PROTOCOL),
            ("scientific_context_intent", ARTIFACT_CONTEXT_PROTOCOL),
        )
        for name, protocol in fields:
            if name in value:
                _scientific_intent(value[name], protocol)
        if "explore_collection_intent" in value:
            _explore_collection_intent(value["explore_collection_intent"])
            if value.get("mode", "explore") != "explore" or value.get("parent_result_ids"):
                raise ValueError
        if ("scientific_context_intent" in value) != ("scientific_context_sha256" in value):
            raise ValueError
        if "scientific_context_sha256" in value:
            digest = value["scientific_context_sha256"]
            if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                raise ValueError
        for key in ("runs", "jobs"):
            if isinstance(value.get(key), list):
                for item in value[key]:
                    _scientific_history(item)
        return value
    except (ValueError, TypeError, KeyError, AttributeError, ClientError):
        raise ClientError("invalid_scientific_history_response") from None


def _artifact_scientific_context(value):
    if not isinstance(value, dict) or "scientific_context" not in value:
        return value
    try:
        binding = value["scientific_context"]
        if not isinstance(binding, dict) or set(binding) != {
            "context",
            "context_sha256",
        }:
            raise ValueError
        context, digest = binding["context"], binding["context_sha256"]
        if (
            not isinstance(context, dict)
            or set(context)
            != {
                "protocol",
                "source_reference",
                "source_revision",
                "stage_origin",
                "ancestry_nodes",
                "ancestry_edges",
                "prior_formalisation",
            }
            or context["protocol"] != ARTIFACT_CONTEXT_PROTOCOL
            or not isinstance(digest, str)
            or not DIGEST.fullmatch(digest)
        ):
            raise ValueError
        encoded = json.dumps(
            context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded) > 262_144 or hashlib.sha256(encoded).hexdigest() != digest:
            raise ValueError
        reference = _discovery_reference(context["source_reference"])
        if reference != {
            "result_id": value.get("source_result_id"),
            "run_id": value.get("source_run_id"),
            "content_sha256": value.get("source_sha256"),
        }:
            raise ValueError
        revision, nodes, edges = (
            context["source_revision"],
            context["ancestry_nodes"],
            context["ancestry_edges"],
        )
        if (
            not isinstance(revision, dict)
            or set(revision)
            != {
                "root_reference",
                "current_reference",
                "revision",
                "state_at_admission",
                "applied_assessments",
            }
            or revision["current_reference"] != reference
            or revision["state_at_admission"] != "supported"
            or not isinstance(revision["applied_assessments"], list)
            or len(revision["applied_assessments"]) != _integer(revision["revision"], 0, 64)
            or not isinstance(nodes, list)
            or not 1 <= len(nodes) <= 128
            or not isinstance(edges, list)
            or len(edges) > 512
        ):
            raise ValueError
        _discovery_reference(revision["root_reference"])
        cursor, supported = revision["root_reference"], True
        for ordinal, assessment in enumerate(revision["applied_assessments"], 1):
            if (
                not isinstance(assessment, dict)
                or assessment.get("applied") is not True
                or assessment.get("head_revision") != ordinal
                or type(assessment.get("head_revision")) is not int
                or assessment.get("prior_reference") != cursor
                or assessment.get("disposition") not in ("unchanged", "revised", "unsupported")
            ):
                raise ValueError
            canonical_uuid(assessment["assessment_id"])
            canonical_uuid(assessment["run_id"])
            candidate = assessment.get("candidate_reference")
            if (assessment["disposition"] == "revised") != (candidate is not None):
                raise ValueError
            if candidate is not None:
                candidate = _discovery_reference(candidate)
                if candidate["run_id"] != assessment["run_id"]:
                    raise ValueError
                cursor, supported = candidate, True
            elif assessment["disposition"] == "unsupported":
                supported = False
        if cursor != reference or not supported:
            raise ValueError

        def stage_origin(origin, exact_reference=None):
            if origin is None:
                return None
            if not isinstance(origin, dict) or set(origin) != {
                "receipt",
                "receipt_sha256",
                "relationship",
                "revision_of",
            }:
                raise ValueError
            receipt = origin["receipt"]
            _discovery_receipt(receipt, canonical_uuid(receipt["run_id"]))
            receipt_digest = hashlib.sha256(
                json.dumps(
                    receipt,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            receipt_reference = {
                "result_id": receipt["result_id"],
                "run_id": receipt["run_id"],
                "content_sha256": receipt["result_sha256"],
            }
            if receipt_digest != origin["receipt_sha256"] or origin["relationship"] not in (
                "exact_stage_result",
                "reassessed_descendant",
            ):
                raise ValueError
            if origin["revision_of"] is not None:
                _discovery_reference(origin["revision_of"])
            if exact_reference is not None and (
                receipt_reference != exact_reference
                or origin["relationship"] != "exact_stage_result"
            ):
                raise ValueError
            return receipt_reference

        original = stage_origin(context["stage_origin"])
        if original is not None and (
            (original == reference)
            != (context["stage_origin"]["relationship"] == "exact_stage_result")
        ):
            raise ValueError
        known = {}
        for node in nodes:
            if not isinstance(node, dict) or set(node) != {
                "reference",
                "finding",
                "stage_origin",
                "overview_binding",
            }:
                raise ValueError
            ref = _discovery_reference(node["reference"])
            if ref["result_id"] in known:
                raise ValueError
            known[ref["result_id"]] = ref
            if not isinstance(node["finding"], dict) or node["finding"].get("outcome") not in (
                "bridge",
                "answer",
            ):
                raise ValueError
            if node["overview_binding"] is not None:
                _answer_overview(
                    {
                        "finding": node["finding"],
                        "overview_binding": node["overview_binding"],
                    }
                )
            if node["stage_origin"] is not None:
                if node["finding"]["outcome"] != "bridge":
                    raise ValueError
                stage_origin(node["stage_origin"], ref)
        if nodes[0]["reference"] != reference:
            raise ValueError
        adjacency = {identifier: [] for identifier in known}
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) != {
                "child",
                "parent",
                "ordinal",
                "kind",
                "assessment_id",
                "assessment_sha256",
            }:
                raise ValueError
            child, parent = (
                _discovery_reference(edge["child"]),
                _discovery_reference(edge["parent"]),
            )
            if (
                known.get(child["result_id"]) != child
                or known.get(parent["result_id"]) != parent
                or child["result_id"] == parent["result_id"]
                or edge["kind"] not in ("premise", "revision", "reassessment")
            ):
                raise ValueError
            _integer(edge["ordinal"], 0, 4)
            adjacency[child["result_id"]].append(parent["result_id"])
        visiting, visited = set(), set()

        def visit(identifier):
            if identifier in visiting:
                raise ValueError
            if identifier not in visited:
                visiting.add(identifier)
                for parent in adjacency[identifier]:
                    visit(parent)
                visiting.remove(identifier)
                visited.add(identifier)

        visit(reference["result_id"])
        if len(visited) != len(nodes):
            raise ValueError
        return value
    except (ValueError, TypeError, KeyError, AttributeError, ClientError):
        raise ClientError("invalid_artifact_context_response") from None


def _discovery_reference(value):
    if not isinstance(value, dict) or set(value) != {
        "result_id",
        "run_id",
        "content_sha256",
    }:
        raise ValueError
    canonical_uuid(value["result_id"])
    canonical_uuid(value["run_id"])
    if not isinstance(value["content_sha256"], str) or not DIGEST.fullmatch(
        value["content_sha256"]
    ):
        raise ValueError
    return value


def _discovery_receipt(value, run_id):
    if not isinstance(value, dict) or set(value) != {
        "stage_id",
        "run_id",
        "ordinal",
        "result_id",
        "result_sha256",
        "depth",
        "assessment",
        "parent_refs",
        "action_ordinal",
        "model_request_id",
    }:
        raise ValueError
    canonical_uuid(value["stage_id"])
    canonical_uuid(value["result_id"])
    if (
        value["run_id"] != run_id
        or not isinstance(value["result_sha256"], str)
        or not DIGEST.fullmatch(value["result_sha256"])
        or not isinstance(value["model_request_id"], str)
        or not re.fullmatch(r"model-[0-9]{4}", value["model_request_id"])
    ):
        raise ValueError
    _integer(value["ordinal"], 0, 3)
    _integer(value["depth"], 1, 3)
    _integer(value["action_ordinal"], 1, 20)
    parents = value["parent_refs"]
    if not isinstance(parents, list) or len(parents) not in (0, 2, 3, 4):
        raise ValueError
    for parent in parents:
        _discovery_reference(parent)
    if (
        len({parent["result_id"] for parent in parents}) != len(parents)
        or any(parent["result_id"] == value["result_id"] for parent in parents)
        or not parents
        and value["depth"] != 1
    ):
        raise ValueError
    assessment = value["assessment"]
    if not isinstance(assessment, dict) or set(assessment) != {
        "novelty",
        "novelty_rationale",
        "confidence",
        "confidence_score",
        "confidence_scope",
        "confidence_rationale",
        "limitations",
    }:
        raise ValueError
    if (
        assessment["novelty"]
        not in ("known", "partly_known", "not_found_in_bounded_search", "uncertain")
        or assessment["confidence"] not in ("tentative", "moderate", "strong")
        or assessment["confidence_scope"] != "uncalibrated_model_judgement"
        or not isinstance(assessment["limitations"], list)
        or not 1 <= len(assessment["limitations"]) <= 8
    ):
        raise ValueError
    _integer(assessment["confidence_score"], 0, 100)
    for text, maximum in [
        (assessment["novelty_rationale"], 2000),
        (assessment["confidence_rationale"], 2000),
        *((item, 1000) for item in assessment["limitations"]),
    ]:
        if not isinstance(text, str) or not text.strip() or len(text) > maximum:
            raise ValueError
    return value


def _discovery_receipts(values, run_id, intent=None):
    intent = intent or {"max_stages": 4, "max_depth": 3}
    if not isinstance(values, list) or len(values) > intent["max_stages"]:
        raise ValueError
    observed, stage_ids = {}, set()
    prior = None
    for ordinal, value in enumerate(values):
        _discovery_receipt(value, run_id)
        if (
            value["ordinal"] != ordinal
            or value["depth"] > intent["max_depth"]
            or value["stage_id"] in stage_ids
            or value["result_id"] in observed
            or prior
            and (
                value["action_ordinal"] <= prior["action_ordinal"]
                or value["model_request_id"] <= prior["model_request_id"]
            )
        ):
            raise ValueError
        depth = 1
        for parent in value["parent_refs"]:
            if parent["run_id"] == run_id:
                original = observed.get(parent["result_id"])
                if original is None or original["result_sha256"] != parent["content_sha256"]:
                    raise ValueError
                depth = max(depth, original["depth"] + 1)
        if value["depth"] != depth:
            raise ValueError
        observed[value["result_id"]] = value
        stage_ids.add(value["stage_id"])
        prior = value
    return values


def _discovery_detail(value, run_id, stage_id=None, *, mode="surprise"):
    if not isinstance(value, dict) or set(value) != {
        "stage",
        "result",
        "parent_edges",
        "primary_paper_groups",
        "status",
    }:
        raise ValueError
    receipt = _discovery_receipt(value["stage"], run_id)
    if stage_id is not None and receipt["stage_id"] != stage_id:
        raise ValueError
    result, edges = value["result"], value["parent_edges"]
    if (
        value["status"] not in ("current_supported", "superseded", "unsupported", "needs_review")
        or not isinstance(result, dict)
        or result.get("mode") != mode
        or result.get("status") != "bridge"
        or not isinstance(result.get("finding"), dict)
        or result["finding"].get("outcome") != "bridge"
        or not isinstance(edges, list)
        or len(edges) > 5
    ):
        raise ValueError
    support = []
    revision = False
    for ordinal, edge in enumerate(edges):
        if not isinstance(edge, dict) or set(edge) != {
            "kind",
            "ordinal",
            "reference",
            "stage_id",
        }:
            raise ValueError
        reference = _discovery_reference(edge["reference"])
        if (
            type(edge["ordinal"]) is not int
            or edge["ordinal"] != ordinal
            or reference["result_id"] == receipt["result_id"]
            or (reference["run_id"] == run_id) != (edge["stage_id"] is not None)
        ):
            raise ValueError
        if edge["stage_id"] is not None:
            canonical_uuid(edge["stage_id"])
            if edge["stage_id"] == receipt["stage_id"]:
                raise ValueError
        if edge["kind"] == "support" and not revision:
            support.append(reference)
        elif edge["kind"] == "revision" and not revision:
            revision = True
        else:
            raise ValueError
    if support != receipt["parent_refs"]:
        raise ValueError
    return value


def _explore_position(value):
    step = int(value["model_request_id"][-4:])
    _integer(step, 1, 20)
    _integer(value["action_ordinal"], 1, min(step, 16))
    return step


def _explore_receipts(values, run_id, intent=None):
    _discovery_receipts(values, run_id, {"max_stages": 4, "max_depth": 1})
    if intent is not None and len(values) > intent["max_bridges"]:
        raise ValueError
    for receipt in values:
        _explore_position(receipt)
        if receipt["parent_refs"]:
            raise ValueError
    return values


def _explore_rankings(values, run_id, stages=None):
    if not isinstance(values, list) or len(values) > 16:
        raise ValueError
    seen, prior_refs, previous_step, previous_action, previous_coverage = set(), {}, 0, 0, 0
    for ranking in values:
        if (
            not isinstance(ranking, dict)
            or set(ranking)
            != {
                "ranking_id",
                "run_id",
                "packet_sha256",
                "model_request_id",
                "action_ordinal",
                "covered_through_ordinal",
                "ranked",
                "excluded",
            }
            or ranking["run_id"] != run_id
        ):
            raise ValueError
        canonical_uuid(ranking["ranking_id"])
        if (
            ranking["ranking_id"] in seen
            or not isinstance(ranking["packet_sha256"], str)
            or not DIGEST.fullmatch(ranking["packet_sha256"])
            or not isinstance(ranking["model_request_id"], str)
            or not re.fullmatch(r"model-[0-9]{4}", ranking["model_request_id"])
        ):
            raise ValueError
        step = _explore_position(ranking)
        coverage = ranking["covered_through_ordinal"]
        count = 0 if coverage is None else _integer(coverage, 0, 3) + 1
        if (
            step <= previous_step
            or ranking["action_ordinal"] <= previous_action
            or count < previous_coverage
        ):
            raise ValueError
        refs = []
        for name, text_key in (("ranked", "rationale"), ("excluded", "reason")):
            members = ranking[name]
            if not isinstance(members, list) or len(members) > 4:
                raise ValueError
            for member in members:
                if not isinstance(member, dict) or set(member) != {"reference", text_key}:
                    raise ValueError
                ref = _discovery_reference(member["reference"])
                text = member[text_key]
                if (
                    ref["run_id"] != run_id
                    or not isinstance(text, str)
                    or not text.strip()
                    or len(text) > 1500
                ):
                    raise ValueError
                refs.append(ref)
        if len(refs) != count or len({ref["result_id"] for ref in refs}) != len(refs):
            raise ValueError
        current_refs = {ref["result_id"]: ref for ref in refs}
        if any(current_refs.get(identifier) != ref for identifier, ref in prior_refs.items()):
            raise ValueError
        if stages is not None:
            visible = [
                stage for stage in stages if stage["action_ordinal"] < ranking["action_ordinal"]
            ]
            if (
                len(visible) != count
                or any(_explore_position(stage) >= step for stage in visible)
                or {tuple(sorted(ref.items())) for ref in refs}
                != {
                    tuple(
                        sorted(
                            {
                                "run_id": run_id,
                                "result_id": stage["result_id"],
                                "content_sha256": stage["result_sha256"],
                            }.items()
                        )
                    )
                    for stage in visible
                }
            ):
                raise ValueError
            packet = {
                key: ranking[key]
                for key in ("ranked", "excluded", "model_request_id", "action_ordinal")
            }
            packet["visible_receipts"] = visible
            digest = hashlib.sha256(
                json.dumps(
                    packet,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            if digest != ranking["packet_sha256"]:
                raise ValueError
        seen.add(ranking["ranking_id"])
        prior_refs = current_refs
        previous_step, previous_action, previous_coverage = step, ranking["action_ordinal"], count
    return values


def _explore_detail(value, run_id, stage_id=None):
    _discovery_detail(value, run_id, stage_id, mode="explore")
    receipt, result = value["stage"], value["result"]
    _explore_position(receipt)
    if (
        receipt["depth"] != 1
        or receipt["parent_refs"]
        or value["parent_edges"]
        or result.get("field_selections")
        or "overview_binding" in result
    ):
        raise ValueError
    groups = value["primary_paper_groups"]
    if (
        not isinstance(groups, list)
        or len(groups) > 4096
        or any(
            not isinstance(group, dict)
            or set(group) != {"paper_id", "source_ids"}
            or group["paper_id"] != f"paper-{ordinal + 1}"
            or not isinstance(group["source_ids"], list)
            or not group["source_ids"]
            or len(group["source_ids"]) > 4096
            or any(not isinstance(item, str) or not item.strip() for item in group["source_ids"])
            for ordinal, group in enumerate(groups)
        )
    ):
        raise ValueError
    # The receipt hashes the stored envelope, including private immutable source
    # artifact IDs. This DTO rehydrates source bodies and cannot reproduce that
    # envelope hash. Verify the exposed quotes against their exact source bytes.
    sources, quotes = result.get("sources"), result.get("quotes")
    if (
        not isinstance(sources, list)
        or not 1 <= len(sources) <= 64
        or not isinstance(quotes, list)
        or not 2 <= len(quotes) <= 64
    ):
        raise ValueError
    by_id = {}
    for source in sources:
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("source_id"), str)
            or source["source_id"] in by_id
            or not isinstance(source.get("text"), str)
            or not 1 <= len(source["text"]) <= 20_000
            or source.get("content_kind") not in ("abstract", "full_text")
        ):
            raise ValueError
        by_id[source["source_id"]] = source
    evidence = result["finding"].get("evidence")
    if not isinstance(evidence, list) or len(evidence) != len(quotes):
        raise ValueError
    for quote, item in zip(quotes, evidence, strict=True):
        if (
            not isinstance(quote, dict)
            or not isinstance(item, dict)
            or set(quote)
            != {
                "source_id",
                "side",
                "claim_id",
                "quote",
                "supports",
                "start",
                "end",
                "source_sha256",
                "content_kind",
            }
            or set(item) != {"source_id", "side", "quote", "supports"}
        ):
            raise ValueError
        source = by_id.get(quote["source_id"])
        if (
            source is None
            or quote["side"] not in ("left", "right")
            or quote["claim_id"] is not None
            or any(quote[key] != item[key] for key in item)
        ):
            raise ValueError
        start = _integer(quote["start"], 0, len(source["text"]) - 1)
        end = _integer(quote["end"], start + 1, len(source["text"]))
        if (
            source["text"][start:end] != quote["quote"]
            or quote["content_kind"] != source["content_kind"]
            or hashlib.sha256(source["text"].encode("utf-8")).hexdigest() != quote["source_sha256"]
        ):
            raise ValueError
    return value


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)


def _private_info(info):
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or not hasattr(os, "getuid")
        or info.st_uid != os.getuid()
    ):
        raise ClientError("unsafe_private_file")
    return info


def _parent(path):
    descriptor = None
    try:
        path = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
        if not path.name or any(ord(char) < 32 for char in str(path)):
            raise ClientError("unsafe_private_file")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(path.anchor, flags)
        for component in path.parent.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, path.name
    except (OSError, ValueError, TypeError):
        if descriptor is not None:
            os.close(descriptor)
        raise ClientError("private_file_unavailable") from None


def _target_info(directory, name):
    try:
        return _private_info(os.stat(name, dir_fd=directory, follow_symlinks=False))
    except FileNotFoundError:
        return None


def _read_private(path):
    directory, name = _parent(path)
    descriptor = None
    try:
        before = _target_info(directory, name)
        if before is None:
            return None, None
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        actual = _private_info(os.fstat(descriptor))
        if _fingerprint(actual) != _fingerprint(before) or actual.st_size > MAX_COOKIE_BYTES:
            raise ClientError("unsafe_private_file")
        data = os.read(descriptor, MAX_COOKIE_BYTES + 1)
        if (
            len(data) != actual.st_size
            or len(data) > MAX_COOKIE_BYTES
            or _fingerprint(os.fstat(descriptor)) != _fingerprint(actual)
        ):
            raise ClientError("unsafe_private_file")
        return data, _fingerprint(actual)
    except OSError:
        raise ClientError("private_file_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _atomic_private(path, data, *, expected="inspect", allow_replace=True):
    directory, name = _parent(path)
    temporary = None
    lock = None
    try:
        if allow_replace:
            try:
                import fcntl
            except ImportError:
                raise ClientError("protected_files_unsupported") from None
            lock = os.open(
                "." + name + ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            _private_info(os.fstat(lock))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ClientError("private_file_busy") from None
        current = _target_info(directory, name)
        fingerprint = _fingerprint(current) if current else None
        if current is not None and not allow_replace:
            raise ClientError("output_exists")
        if expected != "inspect" and fingerprint != expected:
            raise ClientError("private_file_changed")
        temporary = ".polymath-" + secrets.token_hex(16) + ".tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        latest = _target_info(directory, name)
        if (_fingerprint(latest) if latest else None) != fingerprint:
            raise ClientError("private_file_changed")
        if allow_replace:
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
        else:
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise ClientError("output_exists") from None
            os.unlink(temporary, dir_fd=directory)
            temporary = None
        os.fsync(directory)
        return _fingerprint(_private_info(os.stat(name, dir_fd=directory, follow_symlinks=False)))
    except OSError:
        raise ClientError("private_file_unavailable") from None
    finally:
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
        if lock is not None:
            os.close(lock)
        os.close(directory)


class PolymathClient:
    def __init__(self, base_url, *, cookie_jar=None, timeout=20.0):
        self.origin = canonical_origin(base_url)
        self.base_url = self.origin
        self.timeout = _duration(timeout)
        self.cookie_jar = cookie_jar
        self._jar = http.cookiejar.CookieJar()
        self._loaded, self._ever_owner = False, False
        self._file_fingerprint = None
        self._csrf = None
        self._secrets = set()
        self._connections = set()
        self._timers = {}
        self._responses = {}
        self._stops = {}
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._closed = True
        for stop in tuple(self._stops.values()):
            stop()
        for timer in tuple(self._timers.values()):
            timer.cancel()
        for response in tuple(self._responses.values()):
            response.close()
        for connection in tuple(self._connections):
            connection.close()
        self._connections.clear()
        self._timers.clear()
        self._responses.clear()
        self._stops.clear()

    def _cookie_domain(self):
        host = urlsplit(self.origin).hostname
        return host + ".local" if "." not in host and ":" not in host else host

    def _load_owner(self):
        if self._loaded:
            return
        if self.cookie_jar is not None:
            data, self._file_fingerprint = _read_private(self.cookie_jar)
            if data is not None:
                try:
                    lines = data.decode("ascii").splitlines()
                    if (
                        not lines
                        or lines[0] != "# Netscape HTTP Cookie File"
                        or len([line for line in lines if line.startswith("# Polymath origin:")])
                        != 1
                        or lines.count("# Polymath origin: " + self.origin) != 1
                    ):
                        raise ValueError
                    cookies = [
                        line
                        for line in lines[1:]
                        if line and (not line.startswith("#") or line.startswith("#HttpOnly_"))
                    ]
                    if len(cookies) != 1:
                        raise ValueError
                    domain, subdomains, path, secure, expires, name, value = (
                        cookies[0].removeprefix("#HttpOnly_").split("\t")
                    )
                    if (
                        domain != self._cookie_domain()
                        or subdomains != "FALSE"
                        or path != "/api/v1"
                        or secure not in ("TRUE", "FALSE")
                        or name != COOKIE_NAME
                        or TOKEN.fullmatch(value) is None
                        or not expires.isascii()
                        or not expires.isdecimal()
                        or len(expires) > 12
                        or (self.origin.startswith("https:") and secure != "TRUE")
                    ):
                        raise ValueError
                    self._jar.set_cookie(
                        http.cookiejar.Cookie(
                            0,
                            name,
                            value,
                            None,
                            False,
                            domain,
                            False,
                            False,
                            path,
                            True,
                            secure == "TRUE",
                            int(expires),
                            False,
                            None,
                            None,
                            {"HttpOnly": None},
                            False,
                        )
                    )
                    self._secrets.add(value)
                    self._ever_owner = True
                except (ValueError, UnicodeError):
                    raise ClientError("invalid_cookie_jar") from None
        self._loaded = True

    def _token(self):
        valid = [
            cookie
            for cookie in self._jar
            if cookie.name == COOKIE_NAME
            and cookie.expires is not None
            and cookie.expires > time.time()
            and (not cookie.secure or self.origin.startswith("https:"))
        ]
        if len(valid) > 1:
            raise ClientError("invalid_cookie_jar")
        return valid[0].value if valid else None

    def _persist_owner(self):
        if self.cookie_jar is None:
            return
        cookie = next((cookie for cookie in self._jar if cookie.name == COOKIE_NAME), None)
        if cookie is None or cookie.expires is None:
            raise ClientError("invalid_session_response")
        data = (
            "# Netscape HTTP Cookie File\n# Polymath origin: "
            + self.origin
            + "\n"
            + "#HttpOnly_"
            + cookie.domain
            + "\tFALSE\t/api/v1\t"
            + ("TRUE" if cookie.secure else "FALSE")
            + "\t"
            + str(cookie.expires)
            + "\t"
            + COOKIE_NAME
            + "\t"
            + cookie.value
            + "\n"
        ).encode("ascii")
        self._file_fingerprint = _atomic_private(
            self.cookie_jar, data, expected=self._file_fingerprint
        )

    def _redact(self, value, depth=0):
        if depth > 64:
            raise ClientError("unexpected_response")
        if isinstance(value, dict):
            return {
                self._redact(key, depth + 1): "[redacted]"
                if key.lower() in SECRET_FIELDS
                else self._redact(item, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact(item, depth + 1) for item in value]
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "[redacted]")
        return value

    def _connect(self, *, deadline):
        parsed = urlsplit(self.origin)
        connection_class = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        kwargs = {"timeout": min(self.timeout, max(0.001, deadline - time.monotonic()))}
        if parsed.scheme == "https":
            kwargs["context"] = ssl.create_default_context()
        connection = connection_class(parsed.hostname, parsed.port, **kwargs)
        self._connections.add(connection)
        return connection

    def _start(self, method, path, *, private=False, body=None, headers=None, duration=None):
        if self._closed:
            raise ClientError("client_closed")
        if not path.startswith("/api/v1/") or any(ord(char) < 32 for char in path):
            raise ClientError("invalid_endpoint")
        request_headers = {"Accept": "application/json", **(headers or {})}
        if private:
            self._load_owner()
            token = self._token()
            if token is not None:
                request_headers["Cookie"] = COOKIE_NAME + "=" + token
        deadline = time.monotonic() + (self.timeout if duration is None else duration)
        connection = self._connect(deadline=deadline)
        timer = None
        try:
            connection.connect()
            connection.auto_open = False
            if self._closed:
                raise ClientError("client_closed")
            if time.monotonic() >= deadline:
                raise ClientError("request_timeout")
            active_socket = connection.sock

            def stop():
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            timer = threading.Timer(max(0.001, deadline - time.monotonic()), stop)
            timer.daemon = True
            self._stops[connection] = stop
            timer.start()
            self._timers[connection] = timer
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            self._responses[connection] = response
            return connection, response, timer, deadline
        except Exception as error:
            if timer:
                timer.cancel()
            connection.close()
            self._connections.discard(connection)
            self._timers.pop(connection, None)
            self._stops.pop(connection, None)
            if isinstance(error, ClientError):
                raise
            raise ClientError(
                "write_outcome_unknown" if method == "POST" else "request_failed"
            ) from None

    def _finish(self, connection, response, timer):
        timer.cancel()
        response.close()
        connection.close()
        self._connections.discard(connection)
        self._timers.pop(connection, None)
        self._responses.pop(connection, None)
        self._stops.pop(connection, None)

    def _request(
        self,
        method,
        path,
        *,
        private=False,
        body=None,
        headers=None,
        maximum=MAX_JSON_BYTES,
    ):
        connection, response, timer, deadline = self._start(
            method, path, private=private, body=body, headers=headers
        )
        try:
            if 300 <= response.status < 400:
                raise ClientError("unexpected_redirect", response.status)
            limit = min(maximum, 65_536) if response.status >= 400 else maximum
            chunks, length = [], 0
            while True:
                chunk = response.read1(min(65_536, limit - length + 1))
                if not chunk:
                    break
                length += len(chunk)
                if length > limit:
                    raise ClientError(
                        "write_outcome_unknown" if method == "POST" else "response_too_large",
                        response.status,
                    )
                chunks.append(chunk)
                if time.monotonic() >= deadline:
                    raise ClientError(
                        "write_outcome_unknown" if method == "POST" else "request_timeout"
                    )
            raw = b"".join(chunks)
            if response.status >= 400:
                try:
                    error = json.loads(raw.decode("utf-8")).get("detail")
                except (ValueError, UnicodeError, AttributeError):
                    error = None
                code = (
                    error
                    if isinstance(error, str) and error in SAFE_ERRORS
                    else ("write_outcome_unknown" if method == "POST" else "http_error")
                )
                if method == "POST" and code == "research_storage_unavailable":
                    code = "write_outcome_unknown"
                retry = response.headers.get("Retry-After")
                retry = (
                    int(retry)
                    if isinstance(retry, str)
                    and re.fullmatch(r"[0-9]{1,5}", retry)
                    and int(retry) <= 86400
                    else None
                )
                raise ClientError(code, response.status, retry)
            if not 200 <= response.status < 300:
                raise ClientError("unexpected_response", response.status)
            if response.length not in (None, 0):
                raise ClientError(
                    "write_outcome_unknown" if method == "POST" else "incomplete_response"
                )
            return raw, response.headers
        except ClientError:
            raise
        except Exception:
            raise ClientError(
                "write_outcome_unknown" if method == "POST" else "request_failed"
            ) from None
        finally:
            self._finish(connection, response, timer)

    def _json(self, method, path, *, private=False, body=None, headers=None):
        encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        if encoded is not None:
            headers = {"Content-Type": "application/json", **(headers or {})}
        raw, response_headers = self._request(
            method, path, private=private, body=encoded, headers=headers
        )
        try:
            if response_headers.get_content_type() != "application/json":
                raise ValueError
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeError, RecursionError):
            raise ClientError(
                "write_outcome_unknown" if method == "POST" else "unexpected_response"
            ) from None

    def _session(self, *, explicit=False, allow_new=False):
        self._load_owner()
        previous = self._token()
        if previous is None and not explicit and (self._ever_owner or not allow_new):
            raise ClientError("research_session_required", 401)
        raw, headers = self._request("GET", "/api/v1/session", private=True, maximum=4096)
        try:
            data = json.loads(raw.decode("utf-8"))
            csrf = data["csrf_token"]
            expires = data["expires_at"]
            expiry = datetime.fromisoformat(expires)
            if not isinstance(csrf, str) or DIGEST.fullmatch(csrf) is None or expiry.tzinfo is None:
                raise ValueError
            candidate = http.cookiejar.CookieJar()
            for cookie in self._jar:
                candidate.set_cookie(copy.copy(cookie))

            class CookieResponse:
                def info(self):
                    return headers

            candidate.extract_cookies(CookieResponse(), Request(self.origin + "/api/v1/session"))
            cookies = list(candidate)
            if len(cookies) != 1:
                raise ValueError
            cookie = cookies[0]
            if (
                cookie.name != COOKIE_NAME
                or TOKEN.fullmatch(cookie.value) is None
                or cookie.domain != self._cookie_domain()
                or cookie.domain_specified
                or cookie.path != "/api/v1"
                or cookie.expires is None
                or cookie.expires <= time.time()
                or (self.origin.startswith("https:") and not cookie.secure)
            ):
                raise ValueError
            self._secrets.update((cookie.value, csrf))
            if previous is not None and previous != cookie.value and not explicit:
                raise ClientError("owner_session_changed")
        except (
            ValueError,
            UnicodeError,
            KeyError,
            TypeError,
            AttributeError,
            RecursionError,
        ):
            raise ClientError("invalid_session_response") from None
        self._jar, self._csrf, self._ever_owner = candidate, csrf, True
        self._persist_owner()
        return {"expires_at": expires}

    def init_session(self):
        return self._session(explicit=True)

    def _private_json(self, path):
        self._load_owner()
        if self._token() is None:
            raise ClientError("research_session_required", 401)
        return self._redact(self._json("GET", path, private=True))

    def _write(self, path, body=None, *, allow_new=False):
        self._session(allow_new=allow_new)
        return self._send_write(path, body)

    def _send_write(self, path, body):
        return self._redact(
            self._json(
                "POST",
                path,
                private=True,
                body=body,
                headers={"X-CSRF-Token": self._csrf, "Origin": self.origin},
            )
        )

    def capabilities(self):
        value = self._redact(self._json("GET", "/api/v1/capabilities"))
        if (
            type(value.get("native_research_required")) is not bool
            or type(value.get("research_available")) is not bool
            or type(value.get("meta_configured")) is not bool
        ):
            raise ClientError("invalid_capabilities_response")
        return value

    def research_access(self):
        value = self._private_json("/api/v1/research/access")
        return _research_access(value)

    def research_protocols(self):
        return _research_protocols(self._private_json("/api/v1/research/protocols"))

    def explore_protocols(self):
        return _explore_protocols(self._private_json("/api/v1/research/explore-protocols"))

    def _select_explore_protocol(self, path, body, current_hash):
        if path != "/api/v1/runs" or body.get("mode") != "explore" or body.get("parent_result_ids"):
            return body
        protocols = self.explore_protocols()
        if protocols["activation_sha256"] != current_hash:
            raise ClientError("research_activation_changed", 409)
        approved = protocols["explore_collection_intent"]
        requested = body.get("explore_collection_intent")
        if approved is None:
            if requested is not None:
                raise ClientError("research_explore_collection_not_approved", 409)
            return body
        if requested is not None and requested["max_bridges"] > approved["max_bridges"]:
            raise ClientError("research_explore_collection_not_approved", 409)
        intent = requested or {
            "protocol": EXPLORE_COLLECTION_PROTOCOL,
            "max_bridges": min(3, approved["max_bridges"]),
        }
        return {**body, "explore_collection_intent": dict(intent)}

    def get_assessment_finding(self, result_id):
        identifier = canonical_uuid(result_id)
        value = self._private_json("/api/v1/assessments/findings/" + identifier)
        try:
            if (
                not isinstance(value, dict)
                or value.get("id") != identifier
                or not isinstance(value.get("payload"), dict)
            ):
                raise ValueError
            canonical_uuid(value["run_id"])
            result = value["payload"]
            if (
                result.get("status") not in ("bridge", "answer")
                or result.get("mode") not in ("explore", "ask", "surprise")
                or not isinstance(result.get("finding"), dict)
                or result["finding"].get("outcome") != result["status"]
                or (result["status"] == "answer") != (result["mode"] == "ask")
            ):
                raise ValueError
            _answer_overview(result)
            return value
        except (ValueError, TypeError, KeyError, AttributeError, ClientError):
            raise ClientError("invalid_assessment_finding_response") from None

    def _select_scientific_protocol(self, path, body, current_hash):
        selection = None
        if path == "/api/v1/runs" and body.get("mode") == "ask":
            selection = (
                "ask_claim_overview_v1",
                "ask_overview_intent",
                ASK_OVERVIEW_PROTOCOL,
            )
        elif path == "/api/v1/artifacts/jobs":
            selection = (
                "artifact_scientific_context_v1",
                "scientific_context_intent",
                ARTIFACT_CONTEXT_PROTOCOL,
            )
        elif path == "/api/v1/assessments/jobs":
            selection = (
                "ask_reassessment_overview_v1",
                "assessment_overview_intent",
                ASSESSMENT_OVERVIEW_PROTOCOL,
            )
        if selection is None:
            return body
        protocols = self.research_protocols()
        if protocols["activation_sha256"] != current_hash:
            raise ClientError("research_activation_changed", 409)
        capability, field, protocol = selection
        approved = capability in protocols["capabilities"]
        if path == "/api/v1/assessments/jobs":
            source = self.get_assessment_finding(body["source_result_id"])["payload"]
            if source["status"] != "answer":
                if field in body:
                    raise ClientError("scientific_context_protocol_unavailable", 409)
                return body
            if "overview_binding" in source and not approved:
                raise ClientError("ask_reassessment_overview_required", 409)
        if approved:
            return {**body, field: {"protocol": protocol}}
        if field in body:
            raise ClientError("scientific_context_protocol_unavailable", 409)
        return body

    def _scientific_write(
        self,
        path,
        body,
        *,
        allow_new=False,
        expected_activation_sha256=None,
        require_native=False,
    ):
        if expected_activation_sha256 is not None and (
            not isinstance(expected_activation_sha256, str)
            or not DIGEST.fullmatch(expected_activation_sha256)
        ):
            raise ClientError("invalid_arguments")
        self._session(allow_new=allow_new)
        capabilities = self.capabilities()
        if capabilities["native_research_required"]:
            access = self.research_access()
            if not access["available"]:
                raise ClientError(access["reason"] or "research_access_unavailable", 503)
            if access["audience"] != "public_sessions":
                raise ClientError("research_operator_context_required", 403)
            current_hash = access["expected_activation_sha256"]
            if (
                expected_activation_sha256 is not None
                and expected_activation_sha256 != current_hash
            ):
                raise ClientError("research_activation_changed", 409)
            if not require_native and access["remaining_session_jobs_today"] == 0:
                raise ClientError("public_research_resource_unavailable", 429)
            intent = body.get("discovery_intent")
            if intent is not None:
                approved = access["limits"]["discovery_intent"]
                if approved is None or (
                    intent["max_stages"] > approved["max_stages"]
                    or intent["max_depth"] > approved["max_depth"]
                ):
                    raise ClientError("research_discovery_not_approved", 409)
            body = self._select_scientific_protocol(path, body, current_hash)
            body = self._select_explore_protocol(path, body, current_hash)
            body = {**body, "expected_activation_sha256": current_hash}
        elif require_native or expected_activation_sha256 is not None:
            raise ClientError("research_native_access_required", 409)
        elif not capabilities["research_available"]:
            raise ClientError("research_unavailable", 503)
        elif "explore_collection_intent" in body:
            raise ClientError("research_explore_collection_not_approved", 409)
        elif any(
            field in body
            for field in (
                "ask_overview_intent",
                "scientific_context_intent",
                "assessment_overview_intent",
            )
        ):
            raise ClientError("scientific_context_protocol_unavailable", 409)
        return self._send_write(path, body)

    def public_library(self, query="", limit=20, before=None):
        params = {"q": _query(query), "limit": _integer(limit, 1, 20)}
        if before is not None:
            if not isinstance(before, str) or TOKEN.fullmatch(before) is None:
                raise ClientError("invalid_identifier")
            params["before"] = before
        return self._redact(self._json("GET", "/api/v1/public/library?" + urlencode(params)))

    def public_map(self, query=""):
        return self._redact(
            self._json("GET", "/api/v1/public/map?" + urlencode({"q": _query(query)}))
        )

    def public_finding(self, public_id):
        if not isinstance(public_id, str) or TOKEN.fullmatch(public_id) is None:
            raise ClientError("invalid_identifier")
        return _public_scientific_metadata(
            self._redact(self._json("GET", "/api/v1/public/findings/" + public_id))
        )

    def list_runs(self, limit=20, before=None):
        params = {"limit": _integer(limit, 1, 50)}
        if before is not None:
            params["before"] = canonical_uuid(before)
        return _scientific_history(self._private_json("/api/v1/runs?" + urlencode(params)))

    def get_run(self, run_id):
        return _scientific_history(self._private_json("/api/v1/runs/" + canonical_uuid(run_id)))

    def get_result(self, run_id):
        return _answer_overview(
            self._private_json("/api/v1/runs/" + canonical_uuid(run_id) + "/result")
        )

    def get_discovery(self, run_id):
        identifier = canonical_uuid(run_id)
        value = self._private_json("/api/v1/runs/" + identifier + "/discovery")
        try:
            if (
                set(value)
                != {
                    "run_id",
                    "run_status",
                    "discovery_intent",
                    "stages",
                    "selected_final_stage",
                    "stop_reason",
                    "total_usage",
                    "accounting",
                }
                or value["run_id"] != identifier
            ):
                raise ValueError
            intent = _discovery_intent(value["discovery_intent"])
            stages = _discovery_receipts(value["stages"], identifier, intent)
            if (
                value["run_status"]
                not in (
                    "queued",
                    "running",
                    "waiting_retry",
                    "cancel_requested",
                    "cancelled",
                    "succeeded",
                    "failed",
                    "interrupted",
                )
                or value["selected_final_stage"] is not None
                and value["selected_final_stage"] not in stages
                or value["stop_reason"] is not None
                and (
                    not isinstance(value["stop_reason"], str)
                    or not value["stop_reason"].strip()
                    or len(value["stop_reason"]) > 2000
                )
            ):
                raise ValueError
            usage, accounting = value["total_usage"], value["accounting"]
            if (
                not isinstance(usage, dict)
                or set(usage)
                != {
                    "model_calls",
                    "input_tokens_reported",
                    "output_tokens_reported",
                    "calls_without_usage",
                }
                or not isinstance(accounting, dict)
                or set(accounting)
                != {
                    "counted_spend_microdollars",
                    "open_hold_microdollars",
                    "unknown_hold_microdollars",
                }
            ):
                raise ValueError
            calls = _integer(usage["model_calls"], len(stages), 20)
            _integer(usage["calls_without_usage"], 0, calls)
            for number in [
                usage["input_tokens_reported"],
                usage["output_tokens_reported"],
                *accounting.values(),
            ]:
                if not isinstance(number, str) or not re.fullmatch(
                    r"(?:0|[1-9][0-9]{0,999})", number
                ):
                    raise ValueError
            if int(accounting["unknown_hold_microdollars"]) > int(
                accounting["open_hold_microdollars"]
            ):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, AttributeError, ClientError):
            raise ClientError("invalid_discovery_response") from None

    def list_discovery_stages(self, run_id):
        identifier = canonical_uuid(run_id)
        value = self._private_json("/api/v1/runs/" + identifier + "/discovery/stages")
        try:
            if set(value) != {"run_id", "stages"} or value["run_id"] != identifier:
                raise ValueError
            details = value["stages"]
            if not isinstance(details, list) or len(details) > 4:
                raise ValueError
            receipts = [_discovery_detail(item, identifier)["stage"] for item in details]
            _discovery_receipts(receipts, identifier)
            observed = {item["result_id"]: item for item in receipts}
            for detail in details:
                for edge in detail["parent_edges"]:
                    if edge["stage_id"] is not None:
                        parent = observed.get(edge["reference"]["result_id"])
                        if parent is None or (
                            parent["stage_id"] != edge["stage_id"]
                            or parent["result_sha256"] != edge["reference"]["content_sha256"]
                            or parent["ordinal"] >= detail["stage"]["ordinal"]
                        ):
                            raise ValueError
            return value
        except (ValueError, TypeError, KeyError, AttributeError, ClientError):
            raise ClientError("invalid_discovery_response") from None

    def get_discovery_stage(self, run_id, stage_id):
        identifier, stage = canonical_uuid(run_id), canonical_uuid(stage_id)
        value = self._private_json("/api/v1/runs/" + identifier + "/discovery/stages/" + stage)
        try:
            return _discovery_detail(value, identifier, stage)
        except (ValueError, TypeError, KeyError, AttributeError, ClientError):
            raise ClientError("invalid_discovery_response") from None

    def get_explore_collection(self, run_id):
        identifier = canonical_uuid(run_id)
        value = self._private_json("/api/v1/runs/" + identifier + "/explore-collection")
        try:
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "run_id",
                    "run_status",
                    "explore_collection_intent",
                    "stages",
                    "rankings",
                    "selected_final_ranking",
                    "stop_reason",
                    "total_usage",
                    "accounting",
                }
                or value["run_id"] != identifier
            ):
                raise ValueError
            intent = _explore_collection_intent(value["explore_collection_intent"])
            stages = _explore_receipts(value["stages"], identifier, intent)
            rankings = _explore_rankings(value["rankings"], identifier, stages)
            for key in ("action_ordinal", "model_request_id"):
                if len({item[key] for item in stages + rankings}) != len(stages) + len(rankings):
                    raise ValueError
            final = value["selected_final_ranking"]
            if (
                value["run_status"]
                not in (
                    "queued",
                    "running",
                    "waiting_retry",
                    "cancel_requested",
                    "cancelled",
                    "succeeded",
                    "failed",
                    "interrupted",
                )
                or final is not None
                and (
                    not rankings
                    or final != rankings[-1]
                    or final["covered_through_ordinal"] != (len(stages) - 1 if stages else None)
                )
                or value["stop_reason"] is not None
                and (
                    not isinstance(value["stop_reason"], str)
                    or not value["stop_reason"].strip()
                    or len(value["stop_reason"]) > 2000
                )
            ):
                raise ValueError
            usage, money = value["total_usage"], value["accounting"]
            if (
                not isinstance(usage, dict)
                or set(usage)
                != {
                    "model_calls",
                    "input_tokens_reported",
                    "output_tokens_reported",
                    "calls_without_usage",
                }
                or not isinstance(money, dict)
                or set(money)
                != {
                    "counted_spend_microdollars",
                    "open_hold_microdollars",
                    "unknown_hold_microdollars",
                }
            ):
                raise ValueError
            minimum = max((_explore_position(item) for item in stages + rankings), default=0)
            calls = _integer(usage["model_calls"], minimum, 20)
            _integer(usage["calls_without_usage"], 0, calls)
            for number in [
                usage["input_tokens_reported"],
                usage["output_tokens_reported"],
                *money.values(),
            ]:
                if not isinstance(number, str) or not re.fullmatch(
                    r"(?:0|[1-9][0-9]{0,999})", number
                ):
                    raise ValueError
            if int(money["unknown_hold_microdollars"]) > int(money["open_hold_microdollars"]):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, AttributeError, ClientError):
            raise ClientError("invalid_explore_collection_response") from None

    def list_explore_bridges(self, run_id):
        identifier = canonical_uuid(run_id)
        value = self._private_json("/api/v1/runs/" + identifier + "/bridges")
        try:
            if (
                not isinstance(value, dict)
                or set(value) != {"run_id", "stages"}
                or value["run_id"] != identifier
            ):
                raise ValueError
            details = value["stages"]
            if not isinstance(details, list) or len(details) > 4:
                raise ValueError
            _explore_receipts(
                [_explore_detail(item, identifier)["stage"] for item in details], identifier
            )
            return value
        except (ValueError, TypeError, KeyError, AttributeError, ClientError):
            raise ClientError("invalid_explore_collection_response") from None

    def get_explore_bridge(self, run_id, stage_id):
        identifier, stage = canonical_uuid(run_id), canonical_uuid(stage_id)
        value = self._private_json("/api/v1/runs/" + identifier + "/bridges/" + stage)
        try:
            return _explore_detail(value, identifier, stage)
        except (ValueError, TypeError, KeyError, AttributeError, ClientError):
            raise ClientError("invalid_explore_collection_response") from None

    def list_explore_rankings(self, run_id):
        identifier = canonical_uuid(run_id)
        value = self._private_json("/api/v1/runs/" + identifier + "/rankings")
        try:
            if (
                not isinstance(value, dict)
                or set(value) != {"run_id", "rankings"}
                or value["run_id"] != identifier
            ):
                raise ValueError
            _explore_rankings(value["rankings"], identifier)
            return value
        except (ValueError, TypeError, KeyError, AttributeError, ClientError):
            raise ClientError("invalid_explore_collection_response") from None

    def create_run(
        self,
        mode,
        goal="",
        parent_result_ids=(),
        *,
        discovery_intent=None,
        ask_overview_intent=None,
        explore_collection_intent=None,
        expected_activation_sha256=None,
    ):
        if (
            mode not in ("explore", "ask", "surprise")
            or not isinstance(goal, str)
            or len(goal) > 4000
            or any(ord(char) < 32 and char not in "\n\r\t" for char in goal)
            or (mode != "surprise" and not goal.strip())
            or not isinstance(parent_result_ids, (tuple, list))
            or len(parent_result_ids) > 4
        ):
            raise ClientError("invalid_arguments")
        try:
            goal.encode("utf-8")
        except UnicodeError:
            raise ClientError("invalid_arguments") from None
        parents = [canonical_uuid(value) for value in parent_result_ids]
        if len(set(parents)) != len(parents) or (
            parents and (mode != "explore" or len(parents) < 2)
        ):
            raise ClientError("invalid_arguments")
        body = {"mode": mode, "goal": goal, "parent_result_ids": parents}
        if discovery_intent is not None:
            if mode != "surprise":
                raise ClientError("invalid_arguments")
            body["discovery_intent"] = _discovery_intent(discovery_intent)
        if ask_overview_intent is not None:
            if mode != "ask" or discovery_intent is not None:
                raise ClientError("invalid_arguments")
            body["ask_overview_intent"] = _scientific_intent(
                ask_overview_intent, ASK_OVERVIEW_PROTOCOL
            )
        if explore_collection_intent is not None:
            if (
                mode != "explore"
                or parents
                or discovery_intent is not None
                or ask_overview_intent is not None
            ):
                raise ClientError("invalid_arguments")
            body["explore_collection_intent"] = _explore_collection_intent(
                explore_collection_intent
            )
        return self._scientific_write(
            "/api/v1/runs",
            body,
            allow_new=True,
            expected_activation_sha256=expected_activation_sha256,
        )

    def resume_run(self, run_id, expected_lease_token, expected_activation_sha256):
        identifier = canonical_uuid(run_id)
        if (
            not isinstance(expected_lease_token, str)
            or not re.fullmatch(r"(?:0|[1-9][0-9]{0,18})", expected_lease_token)
            or int(expected_lease_token) > MAX_CURSOR
            or not isinstance(expected_activation_sha256, str)
            or not DIGEST.fullmatch(expected_activation_sha256)
        ):
            raise ClientError("invalid_arguments")
        return self._scientific_write(
            "/api/v1/runs/" + identifier + "/resume",
            {"expected_lease_token": expected_lease_token},
            expected_activation_sha256=expected_activation_sha256,
            require_native=True,
        )

    def cancel_run(self, run_id):
        return self._write("/api/v1/runs/" + canonical_uuid(run_id) + "/cancel")

    def get_artifact(self, artifact_id):
        return _artifact_scientific_context(
            self._private_json("/api/v1/artifacts/" + canonical_uuid(artifact_id))
        )

    def list_artifact_jobs(self, *, source_result_id=None, limit=20, before=None):
        params = {"limit": _integer(limit, 1, 50)}
        if source_result_id is not None:
            params["source_result_id"] = canonical_uuid(source_result_id)
        if before is not None:
            params["before"] = canonical_uuid(before)
        return _scientific_history(
            self._private_json("/api/v1/artifacts/jobs?" + urlencode(params))
        )

    def get_artifact_job(self, run_id):
        return _scientific_history(
            self._private_json("/api/v1/artifacts/jobs/" + canonical_uuid(run_id))
        )

    def create_artifact(
        self,
        kind,
        source_result_id,
        formalisation_id=None,
        *,
        scientific_context_intent=None,
        expected_activation_sha256=None,
    ):
        if kind not in ("formalise", "write_summary", "write_paper"):
            raise ClientError("invalid_arguments")
        body = {"kind": kind, "source_result_id": canonical_uuid(source_result_id)}
        if formalisation_id is not None:
            if kind == "formalise":
                raise ClientError("invalid_arguments")
            body["formalisation_id"] = canonical_uuid(formalisation_id)
        if scientific_context_intent is not None:
            body["scientific_context_intent"] = _scientific_intent(
                scientific_context_intent, ARTIFACT_CONTEXT_PROTOCOL
            )
        return self._scientific_write(
            "/api/v1/artifacts/jobs",
            body,
            expected_activation_sha256=expected_activation_sha256,
        )

    def create_assessment(
        self,
        source_result_id,
        watch_id,
        new_source_version_ids,
        expected_head_revision,
        *,
        assessment_overview_intent=None,
        expected_activation_sha256=None,
    ):
        if (
            not isinstance(new_source_version_ids, (list, tuple))
            or not 1 <= len(new_source_version_ids) <= 5
        ):
            raise ClientError("invalid_arguments")
        versions = [canonical_uuid(value) for value in new_source_version_ids]
        if len(set(versions)) != len(versions):
            raise ClientError("invalid_arguments")
        body = {
            "source_result_id": canonical_uuid(source_result_id),
            "watch_id": canonical_uuid(watch_id),
            "new_source_version_ids": versions,
            "expected_head_revision": _integer(expected_head_revision, 0, 2**31 - 1),
        }
        if assessment_overview_intent is not None:
            body["assessment_overview_intent"] = _scientific_intent(
                assessment_overview_intent, ASSESSMENT_OVERVIEW_PROTOCOL
            )
        return self._scientific_write(
            "/api/v1/assessments/jobs",
            body,
            expected_activation_sha256=expected_activation_sha256,
        )

    def export_artifact(self, artifact_id, output):
        identifier = canonical_uuid(artifact_id)
        metadata = self.get_artifact(identifier)
        digest = metadata.get("markdown_sha256")
        if (
            metadata.get("id") != identifier
            or not isinstance(digest, str)
            or DIGEST.fullmatch(digest) is None
        ):
            raise ClientError("unexpected_response")
        raw, headers = self._request(
            "GET",
            "/api/v1/artifacts/" + identifier + "/export",
            private=True,
            maximum=MAX_EXPORT_BYTES,
        )
        if (
            headers.get_content_type() != "text/markdown"
            or hashlib.sha256(raw).hexdigest() != digest
        ):
            raise ClientError("export_integrity_failed")
        if "markdown" in metadata and (
            not isinstance(metadata["markdown"], str) or metadata["markdown"].encode("utf-8") != raw
        ):
            raise ClientError("export_integrity_failed")
        if str(output) == "-":
            if any(secret.encode() in raw for secret in self._secrets):
                raise ClientError("sensitive_export_content")
            sys.stdout.buffer.write(raw)
        else:
            _atomic_private(output, raw, allow_replace=False)
        return {"markdown_sha256": digest, "byte_count": len(raw)}

    def events(self, run_id, *, after=0, last_event_id=None, max_events=100, max_seconds=30):
        run_id = canonical_uuid(run_id)
        after = _integer(after, 0, MAX_CURSOR)
        if last_event_id is not None:
            after = max(after, _integer(last_event_id, 0, MAX_CURSOR))
        max_events = _integer(max_events, 1, 1000)
        max_seconds = _duration(max_seconds)
        self._load_owner()
        if self._token() is None:
            raise ClientError("research_session_required", 401)
        connection, response, timer, deadline = self._start(
            "GET",
            f"/api/v1/runs/{run_id}/events?" + urlencode({"after": after}),
            private=True,
            headers={"Accept": "text/event-stream", "Last-Event-ID": str(after)},
            duration=max_seconds,
        )
        cursor, count, total = after, 0, 0
        pending = bytearray()
        try:
            if 300 <= response.status < 400:
                raise ClientError("unexpected_redirect", response.status)
            if response.status != 200 or response.headers.get_content_type() != "text/event-stream":
                raise ClientError("event_stream_unavailable", response.status)
            while count < max_events and time.monotonic() < deadline:
                chunk = response.read1(4096)
                if not chunk:
                    if time.monotonic() >= deadline:
                        return
                    if pending.strip():
                        raise ClientError("invalid_event_stream")
                    return
                total += len(chunk)
                if total > MAX_STREAM_BYTES:
                    raise ClientError("event_stream_limit")
                pending.extend(chunk)
                pending = bytearray(pending.replace(b"\r\n", b"\n"))
                while b"\n\n" in pending:
                    frame, _, rest = pending.partition(b"\n\n")
                    pending = bytearray(rest)
                    if len(frame) > MAX_EVENT_BYTES:
                        raise ClientError("event_stream_limit")
                    event = self._event(frame, cursor)
                    if event is None:
                        continue
                    if event["event"] == "activity":
                        cursor = event["id"]
                    count += 1
                    yield self._redact(event)
                    if event["event"] in ("end", "error", "session_expired") or count >= max_events:
                        return
                if len(pending) > MAX_EVENT_BYTES:
                    raise ClientError("event_stream_limit")
        except ClientError:
            raise
        except Exception:
            if time.monotonic() >= deadline:
                return
            raise ClientError("event_stream_failed") from None
        finally:
            self._finish(connection, response, timer)

    def _event(self, frame, cursor):
        try:
            text = frame.decode("utf-8")
            event_type, identifier, data = None, None, []
            for line in text.split("\n"):
                if not line or line.startswith(":"):
                    continue
                name, separator, value = line.partition(":")
                if not separator:
                    raise ValueError
                value = value.removeprefix(" ")
                if name == "event":
                    if event_type is not None:
                        raise ValueError
                    event_type = value
                elif name == "id":
                    if identifier is not None or re.fullmatch(r"[0-9]{1,19}", value) is None:
                        raise ValueError
                    identifier = _integer(int(value), 0, MAX_CURSOR)
                elif name == "data":
                    data.append(value)
                elif name != "retry":
                    raise ValueError
            if event_type is None and not data:
                return None
            if event_type not in (
                "activity",
                "heartbeat",
                "end",
                "error",
                "session_expired",
            ):
                raise ValueError
            payload = json.loads("\n".join(data))
            if not isinstance(payload, dict):
                raise ValueError
            if event_type == "activity":
                sequence = payload.get("sequence")
                if type(sequence) is not int or identifier != sequence or sequence <= cursor:
                    raise ValueError
            elif identifier is not None:
                raise ValueError
            if event_type == "error":
                payload = {
                    "code": payload.get("code")
                    if payload.get("code") in SAFE_ERRORS
                    else "event_stream_failed"
                }
            return {"id": identifier, "event": event_type, "data": payload}
        except (ValueError, UnicodeError, ClientError, RecursionError):
            raise ClientError("invalid_event_stream") from None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ClientError("invalid_arguments")


def parser():
    result = _Parser(description=__doc__)
    result.add_argument("--base-url", default="http://127.0.0.1:8000")
    result.add_argument("--cookie-jar")
    result.add_argument("--timeout", type=float, default=20)
    commands = result.add_subparsers(dest="command")
    public = commands.add_parser("public").add_subparsers(dest="action", required=True)
    library = public.add_parser("library")
    library.add_argument("--query", default="")
    library.add_argument("--limit", type=int, default=20)
    library.add_argument("--before")
    public.add_parser("map").add_argument("--query", default="")
    public.add_parser("finding").add_argument("public_id")
    commands.add_parser("session").add_subparsers(dest="action", required=True).add_parser("init")
    research = commands.add_parser("research").add_subparsers(dest="action", required=True)
    research.add_parser("access")
    research.add_parser("protocols")
    research.add_parser("explore-protocols")
    runs = commands.add_parser("runs").add_subparsers(dest="action", required=True)
    listing = runs.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--before")
    for action in ("show", "result", "cancel"):
        runs.add_parser(action).add_argument("run_id")
    resume = runs.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument("--expected-lease-token", required=True)
    resume.add_argument("--expected-activation-sha256", required=True)
    events = runs.add_parser("events")
    events.add_argument("run_id")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--last-event-id", type=int)
    events.add_argument("--max-events", type=int, default=100)
    events.add_argument("--max-seconds", type=float, default=30)
    create = runs.add_parser("create")
    create.add_argument("--mode", choices=("explore", "ask", "surprise"), required=True)
    goal = create.add_mutually_exclusive_group()
    goal.add_argument("--goal", default="")
    goal.add_argument("--goal-file")
    create.add_argument("--parent-result", action="append", default=[])
    create.add_argument("--discovery-ladder", action="store_true")
    create.add_argument("--max-stages", type=int)
    create.add_argument("--max-depth", type=int)
    create.add_argument("--expected-activation-sha256")
    create.add_argument("--claim-overview", action="store_true")
    create.add_argument("--explore-collection", action="store_true")
    create.add_argument("--max-bridges", type=int)
    discovery = commands.add_parser("discovery").add_subparsers(dest="action", required=True)
    for action in ("show", "stages"):
        discovery.add_parser(action).add_argument("run_id")
    stage = discovery.add_parser("stage")
    stage.add_argument("run_id")
    stage.add_argument("stage_id")
    explore = commands.add_parser("explore").add_subparsers(dest="action", required=True)
    for action in ("show", "bridges", "rankings"):
        explore.add_parser(action).add_argument("run_id")
    bridge = explore.add_parser("bridge")
    bridge.add_argument("run_id")
    bridge.add_argument("stage_id")
    artifacts = commands.add_parser("artifacts").add_subparsers(dest="action", required=True)
    artifacts.add_parser("show").add_argument("artifact_id")
    artifact_create = artifacts.add_parser("create")
    artifact_create.add_argument(
        "--kind", choices=("formalise", "write_summary", "write_paper"), required=True
    )
    artifact_create.add_argument("--source-result", required=True)
    artifact_create.add_argument("--formalisation")
    artifact_create.add_argument("--expected-activation-sha256")
    artifact_create.add_argument("--scientific-context", action="store_true")
    export = artifacts.add_parser("export")
    export.add_argument("artifact_id")
    export.add_argument("--output", required=True)
    assessments = commands.add_parser("assessments").add_subparsers(dest="action", required=True)
    reassess = assessments.add_parser("create")
    reassess.add_argument("--source-result", required=True)
    reassess.add_argument("--watch", required=True)
    reassess.add_argument("--new-source-version", action="append", required=True)
    reassess.add_argument("--expected-head-revision", type=int, required=True)
    reassess.add_argument("--expected-activation-sha256")
    reassess.add_argument("--claim-overview", action="store_true")
    return result


def main(argv=None):
    try:
        arguments = parser()
        args = arguments.parse_args(argv)
        if args.command is None:
            arguments.print_help()
            return 0
        if args.command != "public" and not args.cookie_jar:
            raise ClientError("cookie_jar_required")
        with PolymathClient(
            args.base_url, cookie_jar=args.cookie_jar, timeout=args.timeout
        ) as client:
            if args.command == "public":
                if args.action == "library":
                    value = client.public_library(args.query, args.limit, args.before)
                elif args.action == "map":
                    value = client.public_map(args.query)
                else:
                    value = client.public_finding(args.public_id)
            elif args.command == "session":
                value = client.init_session()
            elif args.command == "research":
                value = {
                    "access": client.research_access,
                    "protocols": client.research_protocols,
                    "explore-protocols": client.explore_protocols,
                }[args.action]()
            elif args.command == "artifacts":
                if args.action == "show":
                    value = client.get_artifact(args.artifact_id)
                elif args.action == "create":
                    value = client.create_artifact(
                        args.kind,
                        args.source_result,
                        args.formalisation,
                        scientific_context_intent={"protocol": ARTIFACT_CONTEXT_PROTOCOL}
                        if args.scientific_context
                        else None,
                        expected_activation_sha256=args.expected_activation_sha256,
                    )
                else:
                    value = client.export_artifact(args.artifact_id, args.output)
                if args.action == "export" and args.output == "-":
                    return 0
            elif args.command == "assessments":
                value = client.create_assessment(
                    args.source_result,
                    args.watch,
                    args.new_source_version,
                    args.expected_head_revision,
                    assessment_overview_intent={"protocol": ASSESSMENT_OVERVIEW_PROTOCOL}
                    if args.claim_overview
                    else None,
                    expected_activation_sha256=args.expected_activation_sha256,
                )
            elif args.command == "discovery":
                if args.action == "show":
                    value = client.get_discovery(args.run_id)
                elif args.action == "stages":
                    value = client.list_discovery_stages(args.run_id)
                else:
                    value = client.get_discovery_stage(args.run_id, args.stage_id)
            elif args.command == "explore":
                if args.action == "show":
                    value = client.get_explore_collection(args.run_id)
                elif args.action == "bridges":
                    value = client.list_explore_bridges(args.run_id)
                elif args.action == "rankings":
                    value = client.list_explore_rankings(args.run_id)
                else:
                    value = client.get_explore_bridge(args.run_id, args.stage_id)
            elif args.action == "list":
                value = client.list_runs(args.limit, args.before)
            elif args.action == "show":
                value = client.get_run(args.run_id)
            elif args.action == "result":
                value = client.get_result(args.run_id)
            elif args.action == "cancel":
                value = client.cancel_run(args.run_id)
            elif args.action == "resume":
                value = client.resume_run(
                    args.run_id,
                    args.expected_lease_token,
                    args.expected_activation_sha256,
                )
            elif args.action == "events":
                for event in client.events(
                    args.run_id,
                    after=args.after,
                    last_event_id=args.last_event_id,
                    max_events=args.max_events,
                    max_seconds=args.max_seconds,
                ):
                    print(json.dumps(event, ensure_ascii=False))
                return 0
            else:
                intent = None
                explore_intent = None
                if args.explore_collection:
                    if (
                        args.mode != "explore"
                        or args.parent_result
                        or args.discovery_ladder
                        or args.claim_overview
                    ):
                        raise ClientError("invalid_arguments")
                    explore_intent = _explore_collection_intent(
                        {
                            "protocol": EXPLORE_COLLECTION_PROTOCOL,
                            "max_bridges": 3 if args.max_bridges is None else args.max_bridges,
                        }
                    )
                elif args.max_bridges is not None:
                    raise ClientError("invalid_arguments")
                if args.discovery_ladder:
                    if args.mode != "surprise":
                        raise ClientError("invalid_arguments")
                    intent = _discovery_intent(
                        {
                            "protocol": "polymath-surprise-ladder-v1",
                            "max_stages": 3 if args.max_stages is None else args.max_stages,
                            "max_depth": 2 if args.max_depth is None else args.max_depth,
                        }
                    )
                elif args.max_stages is not None or args.max_depth is not None:
                    raise ClientError("invalid_arguments")
                goal = args.goal
                if args.goal_file:
                    try:
                        if args.goal_file == "-":
                            raw = sys.stdin.buffer.read(16001)
                        else:
                            with open(args.goal_file, "rb") as source:
                                raw = source.read(16001)
                        if len(raw) > 16000:
                            raise ValueError
                        goal = raw.decode("utf-8")
                    except (OSError, ValueError):
                        raise ClientError("invalid_goal_file") from None
                value = client.create_run(
                    args.mode,
                    goal,
                    args.parent_result,
                    **({"discovery_intent": intent} if intent is not None else {}),
                    **(
                        {"explore_collection_intent": explore_intent}
                        if explore_intent is not None
                        else {}
                    ),
                    **(
                        {"ask_overview_intent": {"protocol": ASK_OVERVIEW_PROTOCOL}}
                        if args.claim_overview
                        else {}
                    ),
                    **(
                        {"expected_activation_sha256": args.expected_activation_sha256}
                        if args.expected_activation_sha256 is not None
                        else {}
                    ),
                )
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
    except ClientError as error:
        data = {"error": error.code, "status": error.status}
        if error.retry_after_seconds is not None:
            data["retry_after_seconds"] = error.retry_after_seconds
        print(json.dumps(data), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, BrokenPipeError):
        return 130
    except Exception:
        print('{"error":"client_failed","status":null}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
