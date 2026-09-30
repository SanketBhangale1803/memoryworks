"""Python client for MemoryWorks."""

from .client import AsyncMemoryWorks, MemoryWorks
from .exceptions import MemoryWorksAPIError, MemoryWorksError
from .models import (
    AskResponse,
    BriefingCitation,
    BriefingOutcomeReceipt,
    BriefingPrecedent,
    BriefingResponse,
    BriefingVerdict,
    ContextEnvelope,
    Evidence,
    OutcomeLabel,
)

# The client was called OrgMemory before the product became MemoryWorks. The
# old names stay importable so existing code keeps working unchanged.
OrgMemory = MemoryWorks
AsyncOrgMemory = AsyncMemoryWorks
OrgMemoryError = MemoryWorksError
OrgMemoryAPIError = MemoryWorksAPIError

__all__ = [
    "AskResponse",
    "AsyncMemoryWorks",
    "BriefingCitation",
    "BriefingOutcomeReceipt",
    "BriefingPrecedent",
    "BriefingResponse",
    "BriefingVerdict",
    "ContextEnvelope",
    "Evidence",
    "MemoryWorks",
    "MemoryWorksAPIError",
    "MemoryWorksError",
    "OrgMemory",
    "OrgMemoryAPIError",
    "OrgMemoryError",
    "AsyncOrgMemory",
    "OutcomeLabel",
]

__version__ = "0.1.0"
