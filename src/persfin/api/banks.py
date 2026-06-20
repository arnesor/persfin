"""Banks router — GET /banks."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query

from persfin.schemas.schemas import AspspsResponse
from persfin.services.enablebanking import get_aspsps

logger = logging.getLogger(__name__)

router = APIRouter(tags=["banks"])

CountryQuery = Annotated[str, Query(description="ISO 3166 two-letter country code")]


@router.get(
    "/banks",
    responses={502: {"description": "Upstream Enable Banking API error"}},
)
def list_banks(country: CountryQuery = "NO") -> AspspsResponse:
    """Return the list of supported banks / ASPSPs for a given country."""
    return get_aspsps(country=country)
