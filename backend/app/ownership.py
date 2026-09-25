"""Who owns a lead -- the one definition of "unclaimed".

"Unclaimed" means a lead the dealership knows about that has no owner
(`Lead.assigned_user_id IS NULL`). An anonymous conversation -- one with no
lead yet -- is never unclaimed: there is nothing to claim until a lead
exists, because `/api/leads/{id}/assign` needs a lead id and takeover only
assigns an owner when the thread already has one. Before this module the
Overview panel counted only leads while the Conversations page's chip also
counted every anonymous started thread (`!c.lead?.assigned_user_id` is true
whenever `c.lead` is null), so the panel and the page its own KPI links to
never agreed once a single anonymous chat existed -- which is the common
case, since a lead is only minted on a booking or a submitted details card.
"""

from __future__ import annotations


def unclaimed():
    """A filter clause for `Lead`."""
    from app.models import Lead

    return Lead.assigned_user_id.is_(None)


def is_unclaimed(lead) -> bool:
    return lead is not None and lead.assigned_user_id is None
