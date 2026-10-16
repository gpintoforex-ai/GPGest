"""Staff records are company scoped and never create login credentials."""
import re
from datetime import timezone

from fastapi import Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import db_session
from .models import Employee, EmployeeStore, Store, now
from .schemas import Name, Strict


class EmployeeEdit(Name):
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=60)
    store_ids: list[str] = Field(min_length=1, max_length=200)

    @field_validator("email", "phone")
    @classmethod
    def trim_contact(cls, value, info):
        value = value.strip()
        if info.field_name == "email":
            value = value.lower()
            if value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                raise ValueError("Indique um email válido ou deixe o campo vazio.")
        return value


class EmployeeStatus(Strict):
    active: bool = Field(strict=True)


def register_routes(app, current, authorize, scope, record, serial):
    def view(db, employee):
        return {**serial(employee), "created_at": employee.created_at.astimezone(timezone.utc).isoformat(), "updated_at": employee.updated_at.astimezone(timezone.utc).isoformat(), "store_ids": sorted(db.scalars(
            select(EmployeeStore.store_id).where(EmployeeStore.employee_id == employee.id)
        ))}

    def existing(db, identifier):
        row = db.get(Employee, identifier)
        if row is None:
            raise HTTPException(404, "Funcionário não encontrado nesta empresa.")
        return row

    def store_ids(db, values):
        identifiers = sorted(set(values))
        valid = set(db.scalars(select(Store.id).where(Store.id.in_(identifiers))))
        if set(identifiers) != valid:
            raise HTTPException(422, "Selecione estabelecimentos válidos desta empresa.")
        return identifiers

    @app.get("/api/employees")
    def list_employees(user=Depends(current), db: Session = Depends(db_session)):
        query = select(Employee).order_by(Employee.name, Employee.id)
        if user.role != "administrador":
            query = query.where(Employee.id.in_(
                select(EmployeeStore.employee_id).where(EmployeeStore.store_id.in_(scope(user, db)))
            ))
        return [view(db, employee) for employee in db.scalars(query)]

    @app.post("/api/employees")
    def create_employee(data: EmployeeEdit, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        identifiers = store_ids(db, data.store_ids)
        employee = Employee(name=data.name, email=data.email, phone=data.phone)
        db.add(employee)
        db.flush()
        db.add_all(EmployeeStore(employee_id=employee.id, store_id=identifier) for identifier in identifiers)
        db.flush()
        result = view(db, employee)
        record(db, user, "funcionario_criado", employee.id, result)
        db.commit()
        return result

    @app.post("/api/employees/{employee_id}")
    def update_employee(employee_id: str, data: EmployeeEdit, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        employee = existing(db, employee_id)
        identifiers = store_ids(db, data.store_ids)
        before = view(db, employee)
        employee.name, employee.email, employee.phone = data.name, data.email, data.phone
        employee.updated_at = now()
        db.execute(delete(EmployeeStore).where(EmployeeStore.employee_id == employee.id))
        db.add_all(EmployeeStore(employee_id=employee.id, store_id=identifier) for identifier in identifiers)
        db.flush()
        result = view(db, employee)
        record(db, user, "funcionario_atualizado", employee.id, {"before": before, "after": result})
        db.commit()
        return result

    @app.post("/api/employees/{employee_id}/status")
    def employee_status(employee_id: str, data: EmployeeStatus, user=Depends(current), db: Session = Depends(db_session)):
        authorize(user, db, roles=["administrador"])
        employee = existing(db, employee_id)
        if employee.active != data.active:
            employee.active = data.active
            employee.updated_at = now()
            record(db, user, "funcionario_ativado" if data.active else "funcionario_inativado", employee.id,
                   {"name": employee.name, "active": data.active})
            db.commit()
        return view(db, employee)
