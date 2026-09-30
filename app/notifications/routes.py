from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.guards import require_credential_store, restore_secret
from app.credentials import CredentialStore, CredentialStoreError
from app.database.models import Notification, NotificationPolicy, RecruitmentEvent
from app.database.session import get_db

from .schemas import (
    IMPORTANT_EVENT_TYPES,
    NotificationOut,
    NotificationPolicyCreate,
    NotificationPolicyOut,
    NotificationPolicyPut,
    NotificationPolicySummary,
    TestNotificationRequest,
)
from .service import (
    PUSH_CREDENTIAL_ID,
    NotificationConfigurationError,
    NotificationService,
    ensure_policy,
)


router = APIRouter(prefix="/api", tags=["notifications"])


def _notification_service(request: Request) -> NotificationService:
    service = getattr(request.app.state, "notification_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="notification service unavailable")
    return service


def _push_credential_id(policy_id: int) -> str:
    # Policy 1 keeps the legacy keyring entry so existing setups stay valid.
    return PUSH_CREDENTIAL_ID if policy_id == 1 else f"notification:push:{policy_id}"


def _has_push_url(policy: NotificationPolicy, store: CredentialStore) -> bool:
    if not policy.push_credential_id:
        return False
    try:
        return bool(store.get(policy.push_credential_id))
    except CredentialStoreError as exc:
        raise HTTPException(status_code=503, detail="credential store unavailable") from exc


def _default_policy_values() -> dict:
    return {
        "configured": False,
        "desktop_enabled": True,
        "push_enabled": False,
        "push_provider": None,
        "has_push_url": False,
        "important_event_types": [item.value for item in IMPORTANT_EVENT_TYPES],
        "notify_actionable_deadlines": True,
        "updated_at": None,
    }


def _policy_summary(
    policy: NotificationPolicy | None,
    store: CredentialStore,
) -> NotificationPolicySummary:
    if policy is None:
        return NotificationPolicySummary(
            **_default_policy_values(),
            id=0,
            name="默认策略",
            enabled=True,
        )
    return NotificationPolicySummary(
        configured=True,
        desktop_enabled=policy.desktop_enabled,
        push_enabled=policy.push_enabled,
        push_provider=policy.push_provider,
        has_push_url=_has_push_url(policy, store),
        important_event_types=policy.important_event_types,
        notify_actionable_deadlines=policy.notify_actionable_deadlines,
        id=policy.id,
        name=policy.name,
        enabled=policy.enabled,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


def _policy_out(
    policy: NotificationPolicy | None,
    store: CredentialStore,
) -> NotificationPolicyOut:
    if policy is None:
        return NotificationPolicyOut(**_default_policy_values())
    return NotificationPolicyOut(
        configured=True,
        desktop_enabled=policy.desktop_enabled,
        push_enabled=policy.push_enabled,
        push_provider=policy.push_provider,
        has_push_url=_has_push_url(policy, store),
        important_event_types=policy.important_event_types,
        notify_actionable_deadlines=policy.notify_actionable_deadlines,
        updated_at=policy.updated_at,
    )


@router.get("/notification-policies", response_model=list[NotificationPolicySummary])
def list_notification_policies(
    request: Request,
    db: Session = Depends(get_db),
) -> list[NotificationPolicySummary]:
    store = require_credential_store(request)
    policies = list(db.scalars(select(NotificationPolicy).order_by(NotificationPolicy.id)).all())
    if not policies:
        # Keep the implicit default policy users had before multi-policy support.
        policy = ensure_policy(db)
        db.commit()
        db.refresh(policy)
        policies = [policy]
    return [_policy_summary(policy, store) for policy in policies]


@router.post("/notification-policies", response_model=NotificationPolicySummary, status_code=201)
def create_notification_policy(
    payload: NotificationPolicyCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> NotificationPolicySummary:
    store = require_credential_store(request)
    policy = NotificationPolicy(
        name=payload.name,
        enabled=payload.enabled,
        desktop_enabled=payload.desktop_enabled,
        push_enabled=payload.push_enabled,
        push_provider=payload.push_provider,
        important_event_types=[item.value for item in payload.important_event_types],
        notify_actionable_deadlines=payload.notify_actionable_deadlines,
    )
    db.add(policy)
    db.flush()
    credential_id = _push_credential_id(policy.id)
    old_secret = None
    if payload.push_url is not None:
        try:
            old_secret = store.get(credential_id)
            store.set(credential_id, payload.push_url.get_secret_value())
            policy.push_credential_id = credential_id
        except CredentialStoreError as exc:
            db.rollback()
            raise HTTPException(503, "credential store unavailable") from exc
    try:
        db.commit()
        db.refresh(policy)
    except Exception as exc:
        db.rollback()
        rollback_ok = restore_secret(store, credential_id, old_secret) if policy.push_credential_id else True
        detail = "unable to save notification policy"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(500, detail) from exc
    return _policy_summary(policy, store)


@router.put("/notification-policies/{policy_id}", response_model=NotificationPolicySummary)
def update_notification_policy(
    policy_id: int,
    payload: NotificationPolicyCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> NotificationPolicySummary:
    policy = db.get(NotificationPolicy, policy_id)
    if policy is None:
        raise HTTPException(404, "notification policy not found")
    store = require_credential_store(request)

    action = "set" if payload.push_url is not None else ("clear" if payload.clear_push_url else None)
    target_id = policy.push_credential_id or _push_credential_id(policy.id)
    old_secret = None
    old_credential_id = policy.push_credential_id
    if action:
        try:
            old_secret = store.get(target_id)
            if action == "set":
                store.set(target_id, payload.push_url.get_secret_value())
                policy.push_credential_id = target_id
            else:
                store.delete(target_id)
                policy.push_credential_id = None
        except CredentialStoreError as exc:
            raise HTTPException(503, "credential store unavailable") from exc

    if payload.push_enabled and action != "set":
        try:
            existing = store.get(policy.push_credential_id) if policy.push_credential_id else None
        except CredentialStoreError as exc:
            raise HTTPException(503, "credential store unavailable") from exc
        if not existing:
            raise HTTPException(422, detail="push URL is required when push is enabled")

    old_values = {
        "name": policy.name,
        "enabled": policy.enabled,
        "desktop_enabled": policy.desktop_enabled,
        "push_enabled": policy.push_enabled,
        "push_provider": policy.push_provider,
        "important_event_types": policy.important_event_types,
        "notify_actionable_deadlines": policy.notify_actionable_deadlines,
    }
    policy.name = payload.name
    policy.enabled = payload.enabled
    policy.desktop_enabled = payload.desktop_enabled
    policy.push_enabled = payload.push_enabled
    policy.push_provider = payload.push_provider
    policy.important_event_types = [item.value for item in payload.important_event_types]
    policy.notify_actionable_deadlines = payload.notify_actionable_deadlines
    if action == "set":
        policy.push_credential_id = target_id
    elif action == "clear":
        policy.push_credential_id = None
        policy.push_enabled = False

    try:
        db.commit()
        db.refresh(policy)
    except Exception as exc:
        db.rollback()
        for key, value in old_values.items():
            setattr(policy, key, value)
        policy.push_credential_id = old_credential_id
        rollback_ok = restore_secret(store, target_id, old_secret) if action else True
        detail = "unable to save notification policy"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(500, detail) from exc
    return _policy_summary(policy, store)


@router.delete("/notification-policies/{policy_id}", status_code=204)
def delete_notification_policy(
    policy_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    policy = db.get(NotificationPolicy, policy_id)
    if policy is None:
        raise HTTPException(404, "notification policy not found")
    store = require_credential_store(request)
    credential_id = policy.push_credential_id
    try:
        old_secret = store.get(credential_id) if credential_id else None
        if credential_id:
            store.delete(credential_id)
    except CredentialStoreError as exc:
        raise HTTPException(503, "credential store unavailable") from exc
    try:
        db.delete(policy)
        db.commit()
    except Exception as exc:
        db.rollback()
        rollback_ok = restore_secret(store, credential_id, old_secret) if credential_id else True
        detail = "unable to delete notification policy"
        if not rollback_ok:
            detail += "; credential rollback failed"
        raise HTTPException(500, detail) from exc


@router.post("/notification-policies/{policy_id}/test", response_model=NotificationOut)
def test_notification_policy(
    policy_id: int,
    payload: TestNotificationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> Notification:
    policy = db.get(NotificationPolicy, policy_id)
    if policy is None:
        raise HTTPException(404, "notification policy not found")
    try:
        return _notification_service(request).create_test_notification(
            db, payload.provider, policy=policy
        )
    except NotificationConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/notification-policy", response_model=NotificationPolicyOut)
def get_notification_policy(
    request: Request,
    db: Session = Depends(get_db),
) -> NotificationPolicyOut:
    return _policy_out(db.get(NotificationPolicy, 1), require_credential_store(request))


@router.put("/notification-policy", response_model=NotificationPolicyOut)
def put_notification_policy(
    payload: NotificationPolicyPut,
    request: Request,
    db: Session = Depends(get_db),
) -> NotificationPolicyOut:
    store = require_credential_store(request)
    policy = db.get(NotificationPolicy, 1)
    old_credential_id = policy.push_credential_id if policy else None
    action = "set" if payload.push_url is not None else (
        "clear" if payload.clear_push_url else None
    )
    target_id = old_credential_id or PUSH_CREDENTIAL_ID
    old_secret = None
    if action:
        try:
            old_secret = store.get(target_id)
            if action == "set":
                store.set(PUSH_CREDENTIAL_ID, payload.push_url.get_secret_value())
                target_id = PUSH_CREDENTIAL_ID
            else:
                store.delete(target_id)
        except CredentialStoreError as exc:
            raise HTTPException(503, "credential store unavailable") from exc

    if payload.push_enabled and action != "set":
        try:
            existing_target = (
                store.get(old_credential_id) if old_credential_id is not None else None
            )
        except CredentialStoreError as exc:
            raise HTTPException(503, "credential store unavailable") from exc
        if not existing_target:
            raise HTTPException(422, detail="push URL is required when push is enabled")

    if policy is None:
        policy = ensure_policy(db)
    policy.desktop_enabled = payload.desktop_enabled
    policy.push_enabled = payload.push_enabled
    policy.push_provider = payload.push_provider
    policy.important_event_types = [item.value for item in payload.important_event_types]
    policy.notify_actionable_deadlines = payload.notify_actionable_deadlines
    if action == "set":
        policy.push_credential_id = PUSH_CREDENTIAL_ID
    elif action == "clear":
        policy.push_credential_id = None
        policy.push_enabled = False

    try:
        db.commit()
        db.refresh(policy)
    except Exception as exc:
        db.rollback()
        if action:
            try:
                if old_secret is None:
                    store.delete(target_id)
                else:
                    store.set(target_id, old_secret)
            except CredentialStoreError:
                raise HTTPException(
                    500,
                    detail="unable to save notification policy; credential rollback failed",
                ) from exc
        raise HTTPException(500, detail="unable to save notification policy") from exc
    return _policy_out(policy, store)


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    notification_status: str | None = Query(default=None, alias="status"),
    provider: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[Notification]:
    statement = select(Notification)
    if notification_status:
        statement = statement.where(Notification.status == notification_status)
    if provider:
        statement = statement.where(Notification.provider == provider)
    return list(db.scalars(statement.order_by(Notification.id.desc()).limit(limit)).all())


@router.post("/notifications/test", response_model=NotificationOut)
def test_notification(
    payload: TestNotificationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> Notification:
    try:
        return _notification_service(request).create_test_notification(db, payload.provider)
    except NotificationConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/notifications/{notification_id}/retry", response_model=NotificationOut)
def retry_notification(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="notification not found")
    if notification.status == "sent":
        raise HTTPException(status_code=409, detail="notification has already been sent")
    return _notification_service(request).send_notification(
        db,
        notification,
        manual=True,
    )


@router.post(
    "/recruitment-events/{event_id}/notify",
    response_model=list[NotificationOut],
)
def notify_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> list[Notification]:
    event = db.get(RecruitmentEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="recruitment event not found")
    return _notification_service(request).notify_event(db, event)
