from book_agent.schemas.common import BaseSchema


class DocumentContractResponse(BaseSchema):
    supported_source_types: list[str]
    current_phase: str
    notes: list[str]

