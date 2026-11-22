"""Price schedule: hold the start price, then lower it step by step down to a floor.

Two ways to size the steps: `count` equal steps from start to floor (default; the last step
lands exactly on the floor), or a fixed `step` per reduction (euros or percent; the last step
is cut short at the floor).

Pure functions only, so the schedule can be tested without a browser.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class PriceSchedule:
    start: int            # price at publication (euros)
    floor: int            # never go below this
    hold_days: int = 14   # keep the start price this long
    step: str = "5"       # "5" = minus 5 euros, "10%" = minus 10 percent of the current price
    every_days: int = 7   # one step per this many days after the hold
    count: int = 0        # > 0: this many equal steps from start to floor (step is then ignored)

    def __post_init__(self) -> None:
        if self.floor < 0 or self.start < 0:
            raise ValueError("prices must not be negative")
        if self.floor > self.start:
            raise ValueError(f"floor {self.floor} is above start price {self.start}")
        if self.every_days < 1:
            raise ValueError("every_days must be at least 1")
        if self.hold_days < 0:
            raise ValueError("hold_days must not be negative")
        if self.count < 0:
            raise ValueError("count must not be negative")
        if not self.count:
            _parse_step(self.step)


def _parse_step(step: str) -> tuple[str, Decimal]:
    s = str(step).strip()
    if s.endswith("%"):
        value = Decimal(s[:-1].strip())
        if not 0 < value < 100:
            raise ValueError(f"percent step must be between 0 and 100: {step!r}")
        return "percent", value
    value = Decimal(s)
    if value <= 0:
        raise ValueError(f"step must be positive: {step!r}")
    return "fixed", value


def _round_euro(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def steps_due(schedule: PriceSchedule, days_online: int) -> int:
    """How many reductions are due after `days_online` days in the current cycle."""
    if days_online < schedule.hold_days:
        return 0
    return (days_online - schedule.hold_days) // schedule.every_days + 1


def price_after(schedule: PriceSchedule, steps: int) -> int:
    """Price after `steps` reductions, rounded to whole euros, never below the floor."""
    if schedule.count:
        if steps >= schedule.count:
            return schedule.floor
        gap = Decimal(schedule.start - schedule.floor)
        return _round_euro(Decimal(schedule.start) - gap * max(0, steps) / schedule.count)
    kind, amount = _parse_step(schedule.step)
    price = Decimal(schedule.start)
    for _ in range(max(0, steps)):
        if kind == "percent":
            price = Decimal(_round_euro(price * (Decimal(100) - amount) / Decimal(100)))
        else:
            price = price - amount
        if price <= schedule.floor:
            return schedule.floor
    return max(schedule.floor, _round_euro(price))


def target_price(schedule: PriceSchedule, days_online: int) -> int:
    return price_after(schedule, steps_due(schedule, days_online))


def at_floor(schedule: PriceSchedule, days_online: int) -> bool:
    return target_price(schedule, days_online) <= schedule.floor
