import math

from trading import compute_drawdown, compute_tp_sl_prices


def test_compute_drawdown_long_loss():
    dd = compute_drawdown(entry_price=100, current_price=90, is_long=True, leverage=2)
    assert math.isclose(dd, 20.0, rel_tol=1e-3)


def test_compute_drawdown_short_loss():
    dd = compute_drawdown(entry_price=100, current_price=110, is_long=False, leverage=3)
    assert math.isclose(dd, 30.0, rel_tol=1e-3)


def test_compute_drawdown_profit_zero():
    dd = compute_drawdown(entry_price=100, current_price=120, is_long=True, leverage=5)
    assert dd == 0.0


def test_tp_sl_prices_long():
    tp, sl = compute_tp_sl_prices(
        entry_price=100, leverage=2, tp_pnl_targets=[5, 10], sl_pnl=-10, is_long=True
    )
    assert len(tp) == 2
    assert tp[0] == 100 * (1 + 0.05 / 2)
    assert sl == 100 * (1 - 0.10 / 2)


def test_tp_sl_prices_short_high_targets():
    tp, sl = compute_tp_sl_prices(
        entry_price=200,
        leverage=4,
        tp_pnl_targets=[50, 100, 150],
        sl_pnl=-50,
        is_long=False,
    )
    # Mouvement de prix = target/leverage
    assert len(tp) == 3
    assert math.isclose(tp[0], 200 * (1 - (0.50 / 4)), rel_tol=1e-3)
    assert math.isclose(tp[2], 200 * (1 - (1.50 / 4)), rel_tol=1e-3)
    assert math.isclose(sl, 200 * (1 - (0.50 / 4)), rel_tol=1e-3)


