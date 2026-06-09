"""My HTTP API for Objaverse and Objaverse-XL."""

import math
import threading
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

import objaverse.xl as oxl


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class DownloadRequest(BaseModel):
    file_identifiers: Optional[List[str]] = Field(default=None, min_length=1)
    source: Optional[
        Literal["github", "thingiverse", "smithsonian", "sketchfab"]
    ] = None
    file_type: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=1000)
    download_dir: Optional[str] = "~/.objaverse"
    processes: int = Field(default=1, ge=1, le=256)
    refresh_annotations: bool = False


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    created_at: float
    updated_at: float
    request: Dict[str, Any]
    result: Optional[Dict[str, str]] = None
    error: Optional[str] = None


class AnnotationResponse(BaseModel):
    total_matching: int
    limit: int
    offset: int
    records: List[Dict[str, Any]]


app = FastAPI(
    title="My Objaverse API",
    description="I expose Objaverse-XL metadata and download jobs over HTTP.",
    version="0.1.0",
)

_jobs: Dict[str, JobRecord] = {}
_jobs_lock = threading.Lock()


def _clean_value(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _dataframe_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    records = df.to_dict(orient="records")
    return [
        {key: _clean_value(value) for key, value in record.items()}
        for record in records
    ]


def _load_annotations(download_dir: str, refresh: bool) -> pd.DataFrame:
    return oxl.get_annotations(download_dir=download_dir, refresh=refresh)


def _filter_annotations(
    df: pd.DataFrame,
    source: Optional[str] = None,
    file_type: Optional[str] = None,
    license_name: Optional[str] = None,
    file_identifiers: Optional[List[str]] = None,
) -> pd.DataFrame:
    filtered = df
    if source is not None:
        filtered = filtered[filtered["source"] == source]
    if file_type is not None:
        filtered = filtered[filtered["fileType"].str.lower() == file_type.lower()]
    if license_name is not None:
        filtered = filtered[filtered["license"] == license_name]
    if file_identifiers is not None:
        filtered = filtered[filtered["fileIdentifier"].isin(file_identifiers)]
    return filtered


def _set_job(job: JobRecord) -> None:
    with _jobs_lock:
        _jobs[job.job_id] = job


def _run_download_job(job_id: str, request: DownloadRequest) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job.status = JobStatus.running
        job.updated_at = time.time()
        _jobs[job_id] = job

    try:
        annotations = _load_annotations(
            request.download_dir or "~/.objaverse", request.refresh_annotations
        )
        objects = _filter_annotations(
            annotations,
            source=request.source,
            file_type=request.file_type,
            file_identifiers=request.file_identifiers,
        ).head(request.limit)

        if objects.empty:
            raise ValueError("No matching objects were found for this download request.")

        downloaded = oxl.download_objects(
            objects=objects,
            download_dir=request.download_dir,
            processes=request.processes,
        )

        with _jobs_lock:
            job = _jobs[job_id]
            job.status = JobStatus.succeeded
            job.updated_at = time.time()
            job.result = downloaded
            _jobs[job_id] = job
    except Exception as exc:
        with _jobs_lock:
            job = _jobs[job_id]
            job.status = JobStatus.failed
            job.updated_at = time.time()
            job.error = str(exc)
            _jobs[job_id] = job


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/sources")
def sources() -> Dict[str, List[str]]:
    return {"sources": sorted(oxl.downloaders.keys())}


@app.get("/v1/annotations", response_model=AnnotationResponse)
def annotations(
    source: Optional[
        Literal["github", "thingiverse", "smithsonian", "sketchfab"]
    ] = None,
    file_type: Optional[str] = None,
    license_name: Optional[str] = Query(default=None, alias="license"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    download_dir: str = "~/.objaverse",
    refresh: bool = False,
) -> AnnotationResponse:
    annotations_df = _load_annotations(download_dir, refresh)
    filtered = _filter_annotations(
        annotations_df,
        source=source,
        file_type=file_type,
        license_name=license_name,
    )
    page = filtered.iloc[offset : offset + limit]
    return AnnotationResponse(
        total_matching=len(filtered),
        limit=limit,
        offset=offset,
        records=_dataframe_records(page),
    )


@app.get("/v1/annotations/summary")
def annotations_summary(
    download_dir: str = "~/.objaverse",
    refresh: bool = False,
) -> Dict[str, Any]:
    annotations_df = _load_annotations(download_dir, refresh)
    return {
        "total": int(len(annotations_df)),
        "sources": annotations_df["source"].value_counts().to_dict(),
        "file_types": annotations_df["fileType"].value_counts().head(25).to_dict(),
    }


@app.post("/v1/downloads", response_model=JobRecord, status_code=202)
def create_download_job(
    request: DownloadRequest,
    background_tasks: BackgroundTasks,
) -> JobRecord:
    job_id = str(uuid.uuid4())
    now = time.time()
    job = JobRecord(
        job_id=job_id,
        status=JobStatus.queued,
        created_at=now,
        updated_at=now,
        request=request.dict(),
    )
    _set_job(job)
    background_tasks.add_task(_run_download_job, job_id, request)
    return job


@app.get("/v1/jobs/{job_id}", response_model=JobRecord)
def get_job(job_id: str) -> JobRecord:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
