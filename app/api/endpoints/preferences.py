from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Setting
from app.database.session import get_db

from ..common import PREFERENCE_VALUES, commit, validate_preference
from ..schemas import PreferencesUpdate, SettingIn, SettingOut


router = APIRouter()


@router.put("/settings/preferences", response_model=list[SettingOut])
def preferences_put(payload: PreferencesUpdate, db: Session = Depends(get_db)) -> list[Setting]:
    result = []
    poetry = payload.poetry_preset
    if poetry is None:
        existing_poetry = db.get(Setting, "poetry_preset")
        poetry = existing_poetry.value if existing_poetry is not None else "random"
    for key, value in (("theme", payload.theme), ("density", payload.density), ("poetry_preset", poetry)):
        setting = db.get(Setting, key)
        if setting is None:
            setting = Setting(key=key, value=value)
            db.add(setting)
        else:
            setting.value = value
        result.append(setting)
    # Keep the workbench's richer theme preference aligned with older clients,
    # which can only select light or dark.
    workbench_theme = db.get(Setting, "theme_mode")
    if workbench_theme is None:
        db.add(Setting(key="theme_mode", value=payload.theme))
    else:
        workbench_theme.value = payload.theme
    commit(db, "preferences")
    for setting in result:
        db.refresh(setting)
    return result


@router.put("/settings/{key}", response_model=SettingOut)
def setting_put(key: str, payload: SettingIn, db: Session = Depends(get_db)) -> Setting:
    # This generic endpoint is deliberately limited to non-sensitive UI preferences.
    key = validate_preference(key, payload.value)
    if payload.value is None:
        raise HTTPException(status_code=422, detail="application preference value is required")
    setting = db.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=payload.value)
        db.add(setting)
    else:
        setting.value = payload.value
    if key == "theme":
        workbench_theme = db.get(Setting, "theme_mode")
        if workbench_theme is None:
            db.add(Setting(key="theme_mode", value=payload.value))
        else:
            workbench_theme.value = payload.value
    commit(db, "setting")
    db.refresh(setting)
    return setting


@router.get("/settings", response_model=list[SettingOut])
def setting_list(db: Session = Depends(get_db)) -> list[Setting]:
    return list(
        db.scalars(
            select(Setting)
            .where(Setting.key.in_(PREFERENCE_VALUES))
            .order_by(Setting.key)
        ).all()
    )


@router.get("/settings/{key}", response_model=SettingOut)
def setting_get(key: str, db: Session = Depends(get_db)) -> Setting:
    key = validate_preference(key)
    setting = db.get(Setting, key)
    if setting is None:
        raise HTTPException(status_code=404, detail="setting not found")
    return setting


@router.delete("/settings/{key}", status_code=204)
def setting_delete(key: str, db: Session = Depends(get_db)) -> None:
    setting = setting_get(key, db)
    db.delete(setting)
    commit(db, "setting")
