"""Platform adapters, registered onto the extraction ladder.

One module per platform, and each registers itself here rather than being
imported at the point of use -- `extract.extract_list` walks a list and knows
nothing about who is on it, which is what makes adding a site a file rather
than a refactor.

Which dealer's lot to keep is configuration, not code: a DCS site can list
several stores on one page and this app holds exactly one dealership, so
SCRAPER_DEALER_ID narrows it.
"""

from app.config import settings
from app.ingest.extract import LIST_ADAPTERS
from app.ingest.sites.dealercarsearch import DealerCarSearch

LIST_ADAPTERS.append(DealerCarSearch(dealer_id=settings.scraper_dealer_id))

__all__ = ["DealerCarSearch"]
