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
    "meta_research_input_changed": 409,
    "meta_research_outcome_unknown": 409,
    "meta_research_storage_unavailable": 503,
}


class MetaResearchError(Exception):
    """Only static control codes cross the private HTTP boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
