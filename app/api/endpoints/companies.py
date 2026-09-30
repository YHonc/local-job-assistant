from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Company
from app.database.session import get_db

from ..common import commit, require_company
from ..schemas import CompanyIn, CompanyOut, CompanyPatch


router = APIRouter()


@router.post("/companies", response_model=CompanyOut, status_code=201)
def company_create(payload: CompanyIn, db: Session = Depends(get_db)) -> Company:
    company = Company(**payload.model_dump())
    db.add(company)
    commit(db, "company name")
    db.refresh(company)
    return company


@router.get("/companies", response_model=list[CompanyOut])
def company_list(db: Session = Depends(get_db)) -> list[Company]:
    return list(db.scalars(select(Company).order_by(Company.id)).all())


@router.get("/companies/{company_id}", response_model=CompanyOut)
def company_get(company_id: int, db: Session = Depends(get_db)) -> Company:
    return require_company(db, company_id)


@router.patch("/companies/{company_id}", response_model=CompanyOut)
def company_patch(company_id: int, payload: CompanyPatch, db: Session = Depends(get_db)) -> Company:
    company = require_company(db, company_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(company, key, value)
    commit(db, "company name")
    db.refresh(company)
    return company


@router.delete("/companies/{company_id}", status_code=204)
def company_delete(company_id: int, db: Session = Depends(get_db)) -> None:
    company = require_company(db, company_id)
    if company.jobs:
        raise HTTPException(status_code=409, detail="company has associated jobs")
    db.delete(company)
    commit(db, "company")
