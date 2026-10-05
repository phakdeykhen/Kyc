"""Document adapter registry. Adding a country means adding an adapter here, never editing the core."""

from kyc.documents.adapters.kh_national_id import CambodiaNationalIDAdapter
from kyc.domain.enums import DocumentType

ADAPTERS = {adapter.document_type: adapter for adapter in (CambodiaNationalIDAdapter(),)}


def adapter_for(document_type: DocumentType):
    return ADAPTERS.get(document_type)
