"""`/projects` router: saved workspaces (frontend-architecture-spec.md Section 5).

Mounted in backend/main.py. Opens its own engine on the first request rather than at app
startup, so the rest of the API (stocks, twitter, company graph) still starts without DATABASE_URL.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.ingestion.common.db import make_engine
from backend.models.base import Base
from backend.models.project import Project

router = APIRouter(prefix="/projects", tags=["projects"])

_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_init_lock = asyncio.Lock()


class CamelModel(BaseModel):
    """Snake_case in Python, camelCase on the wire, so the JSON matches frontend/src/types/project.ts."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class ProjectWrite(CamelModel):
    name: str = Field(min_length=1, max_length=200)
    ticker: str | None = None
    graph_snapshot: dict[str, Any] | None = None
    selected_market_ids: list[str] = []
    suggested_markets: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []


class ProjectOut(ProjectWrite):
    id: str
    created_at: datetime
    updated_at: datetime


async def _get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        async with _init_lock:  # concurrent first requests must not each build an engine
            if _sessionmaker is None:
                url = os.environ.get("DATABASE_URL")
                if not url:
                    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Set DATABASE_URL in .env")
                engine = make_engine(url)
                async with engine.begin() as conn:
                    # Only this table: the shared DB's other tables belong to their own packages.
                    await conn.run_sync(Base.metadata.create_all, tables=[Project.__table__])
                _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with (await _get_sessionmaker())() as session:
        yield session


async def _get_or_404(session: AsyncSession, project_id: str) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("", response_model=list[ProjectOut], response_model_by_alias=True)
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[Project]:
    result = await session.scalars(select(Project).order_by(Project.updated_at.desc()))
    return list(result)


@router.get("/{project_id}", response_model=ProjectOut, response_model_by_alias=True)
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)) -> Project:
    return await _get_or_404(session, project_id)


@router.post("", response_model=ProjectOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectWrite, session: AsyncSession = Depends(get_session)) -> Project:
    project = Project(id=str(uuid.uuid4()), **body.model_dump())
    session.add(project)
    await session.commit()
    await session.refresh(project)  # pull server-generated created_at/updated_at
    return project


@router.put("/{project_id}", response_model=ProjectOut, response_model_by_alias=True)
async def update_project(
    project_id: str, body: ProjectWrite, session: AsyncSession = Depends(get_session)
) -> Project:
    project = await _get_or_404(session, project_id)
    for field, value in body.model_dump().items():
        setattr(project, field, value)
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, session: AsyncSession = Depends(get_session)) -> None:
    """Permanent: a project is one row, with its evidence and graph snapshot inside it."""
    project = await _get_or_404(session, project_id)
    await session.delete(project)
    await session.commit()
