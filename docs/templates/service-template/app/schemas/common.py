from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str       # "ok" | "degraded"
    database: str     # "ok" | "error"
