import pytest

from kassist.pricing import PriceSchedule, at_floor, price_after, steps_due, target_price


def test_hold_then_weekly_fixed_steps_down_to_floor():
    s = PriceSchedule(start=49, floor=42, hold_days=14, step="5", every_days=7)
    assert [target_price(s, d) for d in (0, 13, 14, 20, 21, 28, 100)] == [49, 49, 44, 44, 42, 42, 42]


def test_steps_due_counts_one_step_at_end_of_hold():
    s = PriceSchedule(start=100, floor=0, hold_days=10, step="1", every_days=7)
    assert steps_due(s, 9) == 0
    assert steps_due(s, 10) == 1
    assert steps_due(s, 16) == 1
    assert steps_due(s, 17) == 2


def test_percent_steps_round_each_step_half_up():
    s = PriceSchedule(start=150, floor=90, hold_days=0, step="10%", every_days=7)
    assert [price_after(s, n) for n in range(6)] == [150, 135, 122, 110, 99, 90]


def test_never_below_floor_and_at_floor_flag():
    s = PriceSchedule(start=330, floor=270, hold_days=0, step="25", every_days=7)
    assert price_after(s, 3) == 270
    assert at_floor(s, 14)          # 330 -> 305 -> 280 -> 270
    assert not at_floor(s, 0)       # day 0, hold 0: one step, 305 > 270
    assert not at_floor(PriceSchedule(start=330, floor=270, hold_days=7), 0)


def test_zero_hold_reduces_on_day_zero():
    s = PriceSchedule(start=20, floor=10, hold_days=0, step="2", every_days=7)
    assert target_price(s, 0) == 18


@pytest.mark.parametrize("kwargs", [
    dict(start=10, floor=20),
    dict(start=10, floor=5, step="0"),
    dict(start=10, floor=5, step="100%"),
    dict(start=10, floor=5, every_days=0),
    dict(start=10, floor=5, hold_days=-1),
    dict(start=10, floor=5, step="abc"),
])
def test_invalid_schedules_are_rejected(kwargs):
    with pytest.raises(Exception):
        PriceSchedule(**kwargs)


def test_equal_steps_from_start_to_floor():
    s = PriceSchedule(start=49, floor=42, hold_days=14, every_days=7, count=3, step="ignored")
    assert [price_after(s, n) for n in range(5)] == [49, 47, 44, 42, 42]
    assert [target_price(s, d) for d in (13, 14, 21, 28, 60)] == [49, 47, 44, 42, 42]
    one = PriceSchedule(start=330, floor=270, hold_days=0, count=1)
    assert [price_after(one, n) for n in range(3)] == [330, 270, 270]
    assert price_after(PriceSchedule(start=50, floor=50, count=4), 2) == 50


def test_negative_count_rejected():
    with pytest.raises(ValueError):
        PriceSchedule(start=10, floor=5, count=-1)
