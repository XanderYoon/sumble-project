"""Public job and error response shapes."""

from datetime import datetime

from pydantic import BaseModel


class Job(BaseModel):
    """Describe a job with nullable source fields and a required identifier."""

    id: int
    organization_id: int | None = None
    organization_name: str | None = None
    job_title: str | None = None
    location: str | None = None
    country_id: int | None = None
    datetime_pulled: datetime | None = None


class ErrorDetail(BaseModel):
    """Carry a safe failure description and server-generated correlation ID."""

    code: str
    message: str
    request_id: str
    path: str | None = None


class ErrorResponse(BaseModel):
    """Wrap one public error without database diagnostics or credentials."""

    error: ErrorDetail
