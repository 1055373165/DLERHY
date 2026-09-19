from book_agent.schemas.common import BaseSchema


class HealthResponse(BaseSchema):
    status: str


class CapabilitiesResponse(BaseSchema):
    # PDF export needs Playwright and its Chromium; the reader hides the PDF format without them.
    pdf_renderer: bool = False


class MetaResponse(BaseSchema):
    app_name: str
    version: str
    environment: str
    api_prefix: str
    docs_path: str
    capabilities: CapabilitiesResponse = CapabilitiesResponse()

