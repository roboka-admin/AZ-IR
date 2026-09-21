"""Liveness/readiness probes for PaaS platforms."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from ..deps import RepositoryDep, SettingsDep

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness")
def healthz() -> dict[str, Any]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness (checks the data driver)")
def readyz(repo: RepositoryDep, settings: SettingsDep, response: Response) -> dict[str, Any]:
    try:
        stats = repo.stats()
    except Exception as exc:  # pragma: no cover - only on a broken driver
        response.status_code = 503
        return {"status": "unavailable", "driver": repo.driver_name, "detail": str(exc)}
    return {
        "status": "ready",
        "driver": repo.driver_name,
        "env": settings.env,
        "counts": stats,
    }
