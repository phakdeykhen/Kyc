"""Document adapter registry. Adding a country or card means adding an adapter here, never editing the core."""

from kyc.documents.adapters.kh_national_id import CambodiaNationalIDAdapter
from kyc.documents.adapters.kh_nssf import CambodiaNSSFAdapter
from kyc.domain.enums import DocumentType

ADAPTERS = {adapter.document_type: adapter for adapter in (CambodiaNationalIDAdapter(), CambodiaNSSFAdapter())}


def adapter_for(document_type: DocumentType):
    return ADAPTERS.get(document_type)
