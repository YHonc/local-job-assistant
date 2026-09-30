"""Shared, versioned instructions for webpage and saved-draft extraction."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import Setting
from app.database.session import get_db

KEY = "job_extraction_prompt"
FOCUS = {"responsibilities": "岗位职责", "requirements": "任职要求", "salary": "薪资待遇", "location": "工作地点", "experience": "学历与经验", "dates": "招聘时间"}
BASE = "你是一名严谨的招聘信息整理助手。从提供的招聘网页中提取目标岗位，保留事实，不补充未经证实的信息。区分公司介绍、岗位职责和任职要求；忽略推荐职位、导航及广告。"
BUILTINS = [
    {"id": "general", "name": "通用岗位解析", "text": BASE},
    {"id": "technical", "name": "技术岗位", "text": BASE + "\n重点保留技术栈、工程职责，区分必备条件与加分项。"},
    {"id": "campus", "name": "校招与实习", "text": BASE + "\n重点保留毕业年份、实习周期、每周出勤要求和投递截止日期。"},
]


class PersonalTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^custom-[a-zA-Z0-9-]{1,64}$")
    name: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=16000)

    @model_validator(mode="after")
    def nonblank(self):
        self.name = self.name.strip()
        self.text = self.text.strip()
        if not self.name or not self.text:
            raise ValueError("模板名称和内容不能为空")
        return self


class PromptSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=0, ge=0)
    mode: Literal["quick", "full"] = "quick"
    template_id: str = Field(default="general", max_length=80)
    focus: list[Literal["responsibilities", "requirements", "salary", "location", "experience", "dates"]] = Field(default_factory=lambda: ["responsibilities", "requirements", "salary", "location"], max_length=6)
    style: Literal["original", "concise"] = "original"
    additional_rules: str = Field(default="", max_length=4000)
    full_text: str = Field(default="", max_length=16000)
    templates: list[PersonalTemplate] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_config(self):
        ids = [item.id for item in self.templates]
        if len(ids) != len(set(ids)) or len(self.focus) != len(set(self.focus)):
            raise ValueError("模板或提取重点不能重复")
        if self.template_id not in ids + [item["id"] for item in BUILTINS]:
            raise ValueError("模板不存在")
        if self.mode == "full" and not self.full_text.strip():
            raise ValueError("完整提示词不能为空")
        return self


def read_settings(db: Session) -> PromptSettings:
    row = db.get(Setting, KEY)
    return PromptSettings.model_validate_json(row.value) if row else PromptSettings()


def editable_text(config: PromptSettings) -> str:
    if config.mode == "full":
        return config.full_text.strip()
    templates = BUILTINS + [item.model_dump() for item in config.templates]
    base = next(item["text"] for item in templates if item["id"] == config.template_id)
    parts = [base]
    if config.focus:
        parts.append("优先完整提取：" + "、".join(FOCUS[key] for key in config.focus) + "。")
    parts.append("保留原文要点和限定条件。" if config.style == "original" else "简洁整理，合并重复内容，但不丢失限定条件。")
    parts.append("信息缺失时留空，不推测；薪资保留原始范围和单位。")
    if config.additional_rules.strip():
        parts.append(config.additional_rules.strip())
    return "\n".join(parts)


def extraction_system_prompt(db: Session, original: str) -> str:
    # Raw page content stays in the existing untrusted user message, never here.
    if db.get(Setting, KEY) is None:
        return original
    config = read_settings(db)
    return original + "\n用户配置的岗位整理要求（输出格式和网页资料边界仍按上方系统约定）：\n" + editable_text(config)


def output(config: PromptSettings) -> dict:
    return {"config": config.model_dump(), "builtins": BUILTINS, "effective_text": editable_text(config)}


router = APIRouter(prefix="/job-extraction-prompt", tags=["import-prompts"])


@router.get("")
def get_prompt(db: Session = Depends(get_db)):
    return output(read_settings(db))


@router.post("/preview")
def preview_prompt(payload: PromptSettings):
    # Local composition only: no completion, credential access or persistence.
    return {"effective_text": editable_text(payload)}


@router.put("")
def save_prompt(payload: PromptSettings, db: Session = Depends(get_db)):
    row = db.get(Setting, KEY)
    old_value = row.value if row else None
    current = read_settings(db)
    if payload.version != current.version:
        raise HTTPException(409, "提示词已在其他页面更新，请重新加载后再保存。")
    saved = payload.model_copy(update={"version": current.version + 1})
    try:
        if row is None:
            db.add(Setting(key=KEY, value=saved.model_dump_json()))
        else:
            result = db.execute(update(Setting).where(Setting.key == KEY, Setting.value == old_value).values(value=saved.model_dump_json()))
            if result.rowcount != 1:
                db.rollback()
                raise HTTPException(409, "提示词已被更新，请重新加载。")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "提示词已被创建，请重新加载。") from None
    return output(saved)
