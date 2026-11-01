"""Studio concept art: a picture of an invention, painted by Meta's image model.

The flow keeps every paid image on the same budget ledger as research:

1. ``request`` (one transaction): check the owner's image switch, the armed
   research account, the invention and the per-invention, per-visitor and
   daily limits; create one ``render_image`` run capped at one flat-priced call;
   reserve and claim it with a narrow image permit.
2. ``execute``: one request to ``POST /v1/images/generations``. No retries.
3. Finalize (one transaction): a valid image is settled at the flat price and its
   bytes are stored with it; a request Meta rejected (which Meta does not bill)
   is settled at zero; anything ambiguous stays an ``unknown`` hold for the
   operator to reconcile, never assumed free. A render left in flight by a lost
   process becomes ``unknown`` the next time anyone looks.

Prompts are built here from the saved design text, never from browser input.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from polymath.budget import BudgetError, BudgetService, NativeImagePermit
from polymath.budget_models import BudgetAccount, BudgetRunLimit
from polymath.models import ResearchRun
from polymath.studio import StudioError, StudioStore, _bridge, _clip, _programme, _steps
from polymath.studio_models import StudioImage

IMAGES_URL = "https://api.meta.ai/v1/images/generations"
MAX_RESPONSE_BYTES = 12_000_000
MAX_IMAGE_BYTES = 8_000_000
VIEWS = ("concept", "in_use", "schematic", "detail", "sequence", "scale", "origins")
CORE_VIEWS = ("concept", "in_use", "schematic", "detail", "origins")
MAX_IMAGES_PER_INVENTION = 10
PROCESS_WORDS = re.compile(
    r"\b(deploy\w*|cycl\w*|phase\w*|stage[sd]?|sequence\w*|assembl\w*|unfold\w*|"
    r"descen\w*|launch\w*|transform\w*|switch\w*|step\w*|grow\w*|self-repair\w*|"
    r"fold\w*|reconfigur\w*)\b",
    re.IGNORECASE,
)
SCALE_WORDS = re.compile(
    r"\b(swarm|orbit\w*|planet\w*|star|sun|solar system|city|cities|ocean\w*|"
    r"submarine\w*|ship\w*|building\w*|bridge\w*|satellite\w*|tower\w*|dam|"
    r"implant\w*|neural|neuron\w*|cell\w*|micro\w*|nano\w*|molecul\w*|"
    r"capillar\w*|insect\w*|tiny|giant|massive|kilomet\w*)\b",
    re.IGNORECASE,
)
STALE_AFTER = timedelta(minutes=10)
TIMEOUT_SECONDS = 150.0
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


@dataclass(frozen=True)
class ImagePolicy:
    enabled: bool = False
    api_key: SecretStr | None = None
    model: str | None = None
    account_id: str | None = None
    price_microdollars: int = 10_000
    global_daily_limit: int = 400
    session_daily_limit: int = 28

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.api_key and self.model and self.account_id)

    @classmethod
    def from_settings(cls, settings) -> ImagePolicy:
        return cls(
            enabled=settings.paid_meta_image_enabled,
            api_key=settings.model_api_key,
            model=settings.meta_image_model,
            account_id=settings.native_budget_account_id,
            price_microdollars=settings.meta_image_price_microdollars,
            global_daily_limit=settings.image_global_daily_limit,
            session_daily_limit=settings.image_session_daily_limit,
        )


@dataclass(frozen=True)
class Rendered:
    """What one request produced: an image, a rejection Meta does not bill, or unknown."""

    kind: str  # "image" | "refused" | "unknown"
    content: bytes | None = None
    mime: str | None = None
    reason: str = ""


def mime_of(content: bytes) -> str | None:
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return next((mime for signature, mime in SIGNATURES if content.startswith(signature)), None)


def plan_views(brief: str, title: str, explanation: str, mapping_count: int) -> list[str]:
    """The gallery for one invention: five core views, plus up to two chosen from its content.

    *How it works* joins when the design is a process or has many interacting parts;
    *a sense of scale* joins when it is enormous or tiny. Returned in reading order.
    """
    text = f"{brief} {title} {explanation}"
    chosen = set(CORE_VIEWS)
    if mapping_count >= 3 or PROCESS_WORDS.search(text):
        chosen.add("sequence")
    if SCALE_WORDS.search(text):
        chosen.add("scale")
    return [view for view in VIEWS if view in chosen]


def image_prompt(
    view: str,
    title: str,
    explanation: str,
    parts: list[str],
    sources: list[tuple[str, str]] = (),
    brief: str = "",
    mechanism: str = "",
) -> str:
    """A visual brief for one view, built only from the saved design text.

    Each invention's own title, mechanism, parts and sources shape every view, and
    Meta's image model plans the composition from them, so no two inventions look
    alike: a whale-like submarine and a Dyson swarm get entirely different scenes.
    """
    features = "; ".join(part for part in parts if part)[:600]
    name = _clip(title, 200)
    if view == "in_use":
        prompt = (
            f"The new invention {name} at work in the world it was designed for: "
            f"{_clip(brief, 500)}. {_clip(explanation, 1000)} A cinematic, photorealistic scene "
            "with natural light, showing the invention in action in its real setting. "
            "No text, labels or logos."
        )
    elif view == "detail":
        prompt = (
            f"A macro close-up of the key mechanism of a new invention, {name}: "
            f"{_clip(mechanism or features or explanation, 700)}. Show its surface, material and "
            "fine structure in exquisite detail with shallow depth of field, like product "
            "photography of a precision part. No text, labels or logos."
        )
    elif view == "sequence":
        prompt = (
            f"A three-panel illustrated sequence showing how a new invention, {name}, works, "
            f"step by step: {_clip(explanation, 1200)} One consistent, beautifully designed "
            "explainer style across the panels, read from left to right. "
            "No text, numbers or labels."
        )
    elif view == "scale":
        prompt = (
            f"A dramatic sense-of-scale image of a new invention, {name}: "
            f"{_clip(explanation, 800)} Show it beside familiar things, people or its "
            "surroundings so its true size is instantly clear, in a wide, striking "
            "composition. No text, labels or logos."
        )
    elif view == "concept":
        prompt = (
            f"Concept art of a new invention: {_clip(title, 200)}. {_clip(explanation, 1500)} "
            + (f"Key parts: {features}. " if features else "")
            + "Show one realistic, beautifully lit hero render of the invention in the setting "
            "it was made for, in the style of a premium industrial design visualisation. "
            "No text, labels or logos."
        )
    elif view == "schematic":
        prompt = (
            f"An engineering cutaway illustration of a new invention: {_clip(title, 200)}. "
            f"{_clip(explanation, 1200)} "
            + (f"Show how these parts fit together: {features}. " if features else "")
            + "Exploded technical view in an elegant blueprint style: precise white and pale "
            "gold line work on deep navy, with the internal structure and mechanisms clearly "
            "visible. No text, numbers, labels or logos."
        )
    elif view == "origins":
        origins = "; ".join(
            f"{_clip(topic, 160)} (from {_clip(field, 80)})" for field, topic in sources
        )
        prompt = (
            f"A natural-history study plate for a new invention, {_clip(title, 200)}, showing "
            f"where its ideas came from: {_clip(origins, 900) or _clip(explanation, 900)}. "
            "Draw each source as it exists in its own world, beside the part of the invention "
            "it inspired, arranged as one elegant composition in the style of a museum "
            "scientific illustration with soft watercolour and fine ink. No text or labels."
        )
    else:
        raise StudioError("invalid_studio_request")
    return _clip(" ".join(prompt.split()), 3800)


def request_body(model: str, prompt: str, user: str) -> dict:
    return {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": "1536x1024",
        "response_format": "b64_json",
        "output_format": "webp",
        "user": user,
        "tool_enablement": {
            "enable_image_search": False,
            "enable_web_search": False,
            "enable_shell": False,
        },
    }


def render(api_key: str, body: dict, *, transport: httpx.BaseTransport | None = None) -> Rendered:
    """Send exactly one image request and classify the outcome without retrying."""
    try:
        with httpx.Client(
            transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            with client.stream(
                "POST",
                IMAGES_URL,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                json=body,
            ) as response:
                if response.status_code != 200:
                    # Meta does not bill rejected, failed or safety-blocked images.
                    code = "content_policy" if response.status_code == 400 else "rejected"
                    try:
                        raw = response.read()[:4000]
                        if b"content_policy_violation" in raw:
                            code = "content_policy"
                    except httpx.HTTPError:
                        pass
                    return Rendered("refused", reason=f"{code}_{response.status_code}")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        return Rendered("unknown", reason="usage_unavailable")
                    chunks.append(chunk)
    except httpx.ConnectError:
        return Rendered("refused", reason="unreachable")
    except httpx.TimeoutException:
        return Rendered("unknown", reason="timeout")
    except httpx.HTTPError:
        return Rendered("unknown", reason="transport_error")
    try:
        payload = json.loads(b"".join(chunks))
        data = payload["data"]
        if not isinstance(data, list) or len(data) != 1:
            raise ValueError("expected one image")
        content = base64.b64decode(data[0]["b64_json"], validate=True)
    except (ValueError, KeyError, TypeError, binascii.Error):
        return Rendered("unknown", reason="usage_unavailable")
    mime = mime_of(content)
    if mime is None or not 0 < len(content) <= MAX_IMAGE_BYTES:
        return Rendered("unknown", reason="usage_unavailable")
    return Rendered("image", content=content, mime=mime)


class StudioImages:
    def __init__(
        self,
        engine: Engine,
        policy: ImagePolicy,
        *,
        transport: httpx.BaseTransport | None = None,
        budget: BudgetService | None = None,
    ):
        self.engine = engine
        self.policy = policy
        self.transport = transport
        self.budget = budget or BudgetService(engine)

    @property
    def available(self) -> bool:
        return self.policy.configured

    # Requests -----------------------------------------------------------------

    def request(
        self, owner_hash: str, programme_id: uuid.UUID, view: str | None = None
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Reserve and claim one image. Returns (image id, reservation id).

        With no view named, the next missing one is painted (concept art first).
        """
        if view is not None and view not in VIEWS:
            raise StudioError("invalid_studio_request")
        if not self.available:
            raise StudioError("studio_images_unavailable")
        policy = self.policy
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            account = session.get(BudgetAccount, policy.account_id, with_for_update=True)
            if account is None or account.native_scope != "research":
                raise StudioError("studio_images_unavailable")
            self._expire_stale(session)
            StudioStore(self.engine)._absorb(session, programme)
            design = next(
                (step for step in _steps(session, programme) if step.stage == "design"), None
            )
            finding = _bridge(session, design) if design is not None else None
            if finding is None:
                raise StudioError("studio_image_needs_design")
            images = session.scalars(
                select(StudioImage).where(StudioImage.programme_id == programme.id)
            ).all()
            if any(image.status == "rendering" for image in images):
                raise StudioError("studio_image_rendering")
            if sum(image.status != "refused" for image in images) >= MAX_IMAGES_PER_INVENTION:
                raise StudioError("studio_image_limit")
            since = datetime.now(UTC) - timedelta(days=1)
            spent_today = (
                select(func.count())
                .select_from(StudioImage)
                .where(StudioImage.created_at >= since, StudioImage.status != "refused")
            )
            if (
                session.scalar(spent_today) >= policy.global_daily_limit
                or session.scalar(spent_today.where(StudioImage.owner_session_hash == owner_hash))
                >= policy.session_daily_limit
            ):
                raise StudioError("studio_image_daily_limit")
            painted = {image.view for image in images if image.status != "refused"}
            plan = plan_views(
                programme.brief, finding.title, finding.explanation, len(finding.mappings)
            )
            view = view or next((item for item in plan if item not in painted), "concept")
            sources = [
                (held.field_left, held.title)
                for step in _steps(session, programme)
                if step.stage == "inspire" and (held := _bridge(session, step)) is not None
            ]
            first = finding.mappings[0] if finding.mappings else None
            prompt = image_prompt(
                view,
                finding.title,
                finding.explanation,
                [item.right for item in finding.mappings],
                sources,
                brief=programme.brief,
                mechanism=f"{first.right}, inspired by {first.left}" if first else "",
            )
            user = hashlib.sha256(b"polymath-image-user\x00" + owner_hash.encode()).hexdigest()
            body = request_body(policy.model, prompt, user[:32])
            run = ResearchRun(
                id=uuid.uuid4(),
                mode="explore",
                task_kind="render_image",
                status="running",
                goal=_clip(f"Concept art: {finding.title}", 500),
            )
            session.add(run)
            session.flush()
            session.add(
                BudgetRunLimit(
                    account_id=account.id,
                    run_id=run.id,
                    cap_microdollars=policy.price_microdollars,
                    max_calls=1,
                )
            )
            image = StudioImage(
                id=uuid.uuid4(),
                programme_id=programme.id,
                owner_session_hash=owner_hash,
                run_id=run.id,
                prompt=prompt,
                view=view,
            )
            session.add(image)
            session.flush()
            permit = NativeImagePermit(
                account_id=account.id,
                run_id=run.id,
                request_hash=hashlib.sha256(
                    json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            )
            try:
                reservation = self.budget.reserve_in_session(
                    session,
                    account.id,
                    run.id,
                    call_key=f"studio-image-{image.id}",
                    estimated_microdollars=policy.price_microdollars,
                    native_permit=permit,
                )
                if not self.budget.claim_dispatch_in_session(
                    session, reservation.id, native_permit=permit
                ):
                    raise BudgetError("reservation_not_claimable")
            except BudgetError as error:
                raise StudioError("studio_image_budget") from error
            return image.id, reservation.id

    def execute(self, image_id: uuid.UUID, reservation_id: uuid.UUID) -> str:
        """Send the claimed request once, then settle and store in one transaction."""
        with Session(self.engine) as session:
            image = session.get(StudioImage, image_id)
            if image is None or image.status != "rendering":
                return "skipped"
            user = hashlib.sha256(
                b"polymath-image-user\x00" + image.owner_session_hash.encode()
            ).hexdigest()
            body = request_body(self.policy.model, image.prompt, user[:32])
        try:
            outcome = render(self.policy.api_key.get_secret_value(), body, transport=self.transport)
        except Exception:  # A bug must still leave an honest ledger, never a free call.
            outcome = Rendered("unknown", reason="worker_lost")
        return self._finalize(image_id, reservation_id, outcome)

    def _finalize(self, image_id: uuid.UUID, reservation_id: uuid.UUID, outcome: Rendered) -> str:
        with Session(self.engine) as session, session.begin():
            image = session.get(StudioImage, image_id, with_for_update=True)
            run = session.get(ResearchRun, image.run_id, with_for_update=True)
            if image.status != "rendering":
                return image.status
            now = datetime.now(UTC)
            if outcome.kind == "image":
                digest = hashlib.sha256(outcome.content).hexdigest()
                self.budget.settle_in_session(
                    session,
                    reservation_id,
                    actual_microdollars=self.policy.price_microdollars,
                    settlement_key=f"studio-image:{image.id}:{digest[:32]}",
                )
                image.status, image.mime = "succeeded", outcome.mime
                image.content, image.content_sha256 = outcome.content, digest
                run.status, run.outcome = "succeeded", {"image_id": str(image.id)}
            elif outcome.kind == "refused":
                self.budget.settle_in_session(
                    session,
                    reservation_id,
                    actual_microdollars=0,
                    settlement_key=f"studio-image:{image.id}:not-billed",
                )
                image.status, image.error = "refused", outcome.reason[:64]
                run.status = "failed"
            else:
                self.budget.mark_unknown_in_session(session, reservation_id, reason=outcome.reason)
                image.status, image.error = "unknown", outcome.reason[:64]
                run.status = "interrupted"
            image.finished_at = now
            return image.status

    def _expire_stale(self, session: Session) -> None:
        """A render left in flight by a lost process is an unknown hold, never free."""
        cutoff = datetime.now(UTC) - STALE_AFTER
        for image in session.scalars(
            select(StudioImage)
            .where(StudioImage.status == "rendering", StudioImage.created_at < cutoff)
            .with_for_update(skip_locked=True)
        ):
            reservation = self._reservation_for(session, image)
            if reservation is not None and reservation.status in ("reserved", "in_flight"):
                self.budget.mark_unknown_in_session(session, reservation.id, reason="worker_lost")
            image.status, image.error, image.finished_at = (
                "unknown",
                "worker_lost",
                datetime.now(UTC),
            )
            run = session.get(ResearchRun, image.run_id)
            if run is not None:
                run.status = "interrupted"

    @staticmethod
    def _reservation_for(session: Session, image: StudioImage):
        from polymath.budget_models import BudgetReservation

        return session.scalar(
            select(BudgetReservation).where(
                BudgetReservation.run_id == image.run_id,
                BudgetReservation.call_key == f"studio-image-{image.id}",
            )
        )

    # Reads --------------------------------------------------------------------

    def plan(self, owner_hash: str, programme_id: uuid.UUID) -> list[str]:
        """The views chosen for this invention, or none before it is designed."""
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id, lock=True)
            StudioStore(self.engine)._absorb(session, programme)
            design = next(
                (step for step in _steps(session, programme) if step.stage == "design"), None
            )
            finding = _bridge(session, design) if design is not None else None
            if finding is None:
                return []
            return plan_views(
                programme.brief, finding.title, finding.explanation, len(finding.mappings)
            )

    def list(self, owner_hash: str, programme_id: uuid.UUID) -> list[dict]:
        with Session(self.engine) as session, session.begin():
            programme = _programme(session, owner_hash, programme_id)
            self._expire_stale(session)
            rows = session.scalars(
                select(StudioImage)
                .where(StudioImage.programme_id == programme.id)
                .order_by(StudioImage.created_at, StudioImage.id)
            ).all()
            return [
                {
                    "id": str(row.id),
                    "status": row.status,
                    "error": row.error,
                    "view": row.view,
                    "mime": row.mime,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def content(self, owner_hash: str, image_id: uuid.UUID) -> tuple[bytes, str]:
        with Session(self.engine) as session:
            row = session.scalar(
                select(StudioImage).where(
                    StudioImage.id == image_id,
                    StudioImage.owner_session_hash == owner_hash,
                    StudioImage.status == "succeeded",
                )
            )
            if row is None:
                raise StudioError("studio_image_not_found")
            return bytes(row.content), row.mime
