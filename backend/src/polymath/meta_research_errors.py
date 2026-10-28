"""Pure private HTTP control codes; safe to import without store/router cycles."""

META_RESEARCH_ERROR_STATUSES = {
    "meta_research_unavailable": 503,
    "readiness_role_unavailable": 503,
    "meta_research_actor_forbidden": 403,
    "meta_research_not_found": 404,
    "meta_research_invalid_request": 422,
    "meta_research_manifest_changed": 409,
    "meta_research_account_conflict": 409,
    "meta_research_prior_unresolved": 409,
    "meta_research_authority_expired": 409,
    "meta_research_activation_required": 409,
    "meta_research_scientific_protocol_unavailable": 409,
    "meta_research_input_changed": 409,
    "meta_research_outcome_unknown": 409,
    "meta_research_storage_unavailable": 503,
    "public_research_disabled": 503,
    "public_research_resource_unavailable": 503,
    "public_research_requester_expired": 401,
    "public_research_total_budget_exceeded": 503,
    "public_research_daily_budget_exceeded": 429,
    "public_session_total_budget_exceeded": 429,
    "public_session_daily_budget_exceeded": 429,
    "public_session_job_day_limit": 429,
    "public_research_job_day_limit": 429,
    "public_research_active_limit": 429,
    "public_research_queue_limit": 429,
    "public_network_active_limit": 429,
    "active_run_limit": 429,
    "public_research_binding_invalid": 503,
    "public_research_delegation_expired": 503,
}


def retry_headers(error) -> dict[str, str]:
    value = getattr(error, "retry_after_seconds", None)
    return {"Retry-After": str(value)} if type(value) is int and 0 <= value <= 86400 else {}


class MetaResearchError(Exception):
    """Only static control codes cross the private HTTP boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
