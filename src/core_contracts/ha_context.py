"""Positive attribution only. Account IDs and parent links are never proof by themselves."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, StrictInt

from .model import Model

MANUAL_CHANGE_TYPE = "ha.manual_change"

type ProofBasis = Literal["registered_context", "ha_run"]


class HAContext(Model):
    id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=128)
    parent_id: str | None = Field(default=None, max_length=128)


class Attribution(Model):
    kind: Literal["known_automatic", "known_user", "unknown"] = "unknown"
    basis: Literal["registered_context", "ha_run", "interactive", "unknown"] = "unknown"
    matched_context_id: str | None = Field(default=None, max_length=128)
    producer_id: str | None = Field(
        default=None, max_length=128, exclude_if=lambda value: value is None
    )
    automation_id: str | None = Field(
        default=None, max_length=128, exclude_if=lambda value: value is None
    )


class ContextProducer(Model):
    producer_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    display_name: str = Field(min_length=1, max_length=128)
    kind: Literal["policy", "apply"]


class ContextRegistration(Model):
    context: HAContext
    producer_id: str
    ha_epoch_id: str = Field(min_length=1, max_length=128)
    lifetime_s: int = Field(ge=1, le=600)


class EngineRegistration(Model):
    context_id: str = Field(min_length=1, max_length=128)
    producer_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,127}$")
    ha_epoch_id: str = Field(min_length=1, max_length=128)
    lifetime_s: StrictInt = Field(ge=1, le=600)
    valid_until: datetime


@dataclass(frozen=True)
class EngineProof:
    producer_id: str
    registered_at: datetime
    expires_at: datetime


class ContextIndex:
    def __init__(self) -> None:
        self.epoch: str | None = None
        self.links: OrderedDict[str, tuple[HAContext, datetime]] = OrderedDict()
        self.proofs: OrderedDict[str, tuple[ProofBasis, datetime, str | None, str | None]] = (
            OrderedDict()
        )
        # IDs are independent of the full context metadata received with observations.
        self.engine_proofs: dict[str, EngineProof] = {}

    def set_epoch(self, epoch: str | None) -> None:
        if self.epoch != epoch:
            self.links.clear()
            self.proofs.clear()
            self.engine_proofs.clear()
            self.epoch = epoch

    def observe(self, context: HAContext, now: datetime) -> None:
        self._expire(now)
        if context.id is None or self.epoch is None:
            return
        if context.id not in self.links:
            self.links[context.id] = context, now + timedelta(seconds=600)
        elif self.links[context.id][0] != context:
            self.links.pop(context.id, None)
            self.proofs.pop(context.id, None)
            self.engine_proofs.pop(context.id, None)
            return
        while len(self.links) > 1024:
            key, _ = self.links.popitem(last=False)
            self.proofs.pop(key, None)

    def register(
        self,
        context: HAContext,
        now: datetime,
        *,
        lifetime_s: int = 600,
        basis: ProofBasis = "registered_context",
        automation_id: str | None = None,
        producer_id: str | None = None,
    ) -> None:
        if self.epoch is None or context.id is None or not 0 < lifetime_s <= 600:
            raise ValueError("context_registration_invalid")
        self.observe(context, now)
        if context.id not in self.links:
            raise ValueError("context_registration_invalid")
        expires = min(self.links[context.id][1], now + timedelta(seconds=lifetime_s))
        self.proofs[context.id] = basis, expires, automation_id, producer_id
        while len(self.proofs) > 1024:
            self.proofs.popitem(last=False)

    def prepare_engine(
        self, request: EngineRegistration, now: datetime
    ) -> tuple[EngineProof, bool]:
        if self.epoch is None or request.ha_epoch_id != self.epoch:
            raise ValueError("context_epoch_conflict")
        if request.valid_until <= now:
            raise ValueError("context_expired")
        self._expire(now)
        for key, proof in tuple(self.engine_proofs.items()):
            if proof.registered_at + timedelta(seconds=600) <= now:
                del self.engine_proofs[key]
        if prior := self.engine_proofs.get(request.context_id):
            if prior.producer_id != request.producer_id:
                raise ValueError("context_conflict")
            if prior.expires_at <= now:
                raise ValueError("context_expired")
            return prior, False
        if len(self.engine_proofs) >= 1024:
            raise ValueError("busy")
        return EngineProof(
            request.producer_id, now, now + timedelta(seconds=request.lifetime_s)
        ), True

    def chain(self, context: HAContext) -> tuple[str, ...]:
        keys: list[str] = []
        key = context.id
        parent = context.parent_id
        for _ in range(9):
            if key is None:
                return tuple(keys)
            if key in keys:
                return ()
            keys.append(key)
            link = self.links.get(key)
            key = link[0].parent_id if link else parent
            parent = None
        return tuple(keys) if key is None else ()

    def attribution(self, context: HAContext, now: datetime) -> Attribution:
        self.observe(context, now)
        if context.id not in self.links:
            return Attribution()
        keys = self.chain(context)
        for key in keys:
            proof = self.engine_proofs.get(key)
            if proof and now < proof.expires_at:
                return Attribution(
                    kind="known_automatic",
                    basis="registered_context",
                    matched_context_id=key,
                    producer_id=proof.producer_id,
                )
        for key in reversed(keys):
            if key in self.proofs:
                basis, _, automation_id, producer_id = self.proofs[key]
                return Attribution(
                    kind="known_automatic",
                    basis=basis,
                    matched_context_id=key,
                    automation_id=automation_id,
                    producer_id=producer_id,
                )
        return Attribution()

    def clear_registered(self) -> None:
        self.engine_proofs.clear()
        for key, (basis, _, _, _) in tuple(self.proofs.items()):
            if basis == "registered_context":
                del self.proofs[key]

    def _expire(self, now: datetime) -> None:
        for key, (_, until) in tuple(self.links.items()):
            if until <= now:
                del self.links[key]
                self.proofs.pop(key, None)
        for key, (_, until, _, _) in tuple(self.proofs.items()):
            if until <= now:
                del self.proofs[key]
