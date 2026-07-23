from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend.api.errors import ApiError
from backend.api.export_service import (
    ExportCreationError,
    build_study_export,
    cleanup_export_artifact,
)
from backend.application.dependencies import get_application_dependencies


LOGGER = logging.getLogger("study_companion.api")
router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/export", response_class=FileResponse)
def export_study_data() -> FileResponse:
    try:
        artifact = build_study_export(
            workspace_id=get_application_dependencies().workspace_id
        )
    except ExportCreationError as error:
        LOGGER.warning(
            "Study data export failed error_type=%s",
            type(error.__cause__ or error).__name__,
        )
        raise ApiError(
            status_code=500,
            code="export_failed",
            message="Study data export could not be created.",
        ) from error

    return FileResponse(
        path=artifact.archive_path,
        filename=artifact.download_name,
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(cleanup_export_artifact, artifact),
    )
