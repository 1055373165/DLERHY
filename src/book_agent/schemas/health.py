from book_agent.schemas.common import BaseSchema


class HealthResponse(BaseSchema):
    status: str


class MetaResponse(BaseSchema):
    app_name: str
    version: str
    environment: str
    api_prefix: str
    docs_path: str

