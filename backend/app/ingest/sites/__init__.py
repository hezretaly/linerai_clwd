"""Platform adapters, registered onto the extraction ladder.

One module per platform, and each registers itself here rather than being
imported at the point of use -- `extract.extract_list` walks a list and knows
nothing about who is on it, which is what makes adding a site a file rather
than a refactor.

Which dealer's lot to keep is not registered here. A DCS site can list several
stores on one page and this app holds exactly one dealership, but *which* one
is read from the dealership profile per crawl -- so the adapter is registered
unnarrowed and `pipeline.run_ingest` calls `for_dealer()` on the one that
matched. Baked in at import, as it was, the value was whatever the process
started with: correct until somebody switched DEALERSHIP= and restarted
nothing.
"""

from app.ingest.extract import LIST_ADAPTERS
from app.ingest.sites.dealercarsearch import DealerCarSearch

LIST_ADAPTERS.append(DealerCarSearch())

__all__ = ["DealerCarSearch"]
