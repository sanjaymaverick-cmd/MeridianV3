"""Imported holdings become watched names + synthetic equity legs.

Greeks on cash equity: delta ≈ shares, gamma = 0, vega = 0, theta = 0.
Mutual funds / ETFs are watched the same way so risk and reviews can
see them. Options legs stay on the option book.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.storage.schema import Holding, OptionLegRow, WatchItem


def map_into_book(session: Session, holding: Holding) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    existing = session.scalar(select(WatchItem).where(WatchItem.symbol == holding.symbol))
    if existing is None:
        session.add(
            WatchItem(
                symbol=holding.symbol,
                asset_class="fund" if holding.instrument in {"mutual_fund", "etf"} else "equity",
                status="active",
                notes=f"Imported · {holding.account_name}",
                created_at=now,
                updated_at=now,
            )
        )
    if holding.instrument == "option":
        session.add(
            OptionLegRow(
                leg_id=f"imp-{holding.symbol}-{holding.id or 0}",
                symbol=holding.symbol,
                contract_label=holding.company_name,
                lots=holding.quantity,
                multiplier=1.0,
                mark_inr=holding.last_price or holding.avg_cost,
                delta=0.5,
                gamma=0.01,
                vega_per_lot=10.0,
                theta_per_lot=-2.0,
                greeks_as_of=now,
                stale=1,
            )
        )
