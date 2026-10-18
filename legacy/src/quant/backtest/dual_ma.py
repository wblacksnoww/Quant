"""
Dual-MA trend-following strategy (双均线趋势策略).

Signals (long-only, no shorting):
    * fast SMA crosses **above** slow SMA  -> open long
    * fast SMA crosses **below** slow SMA  -> close long

Notes on the backtrader build used here (1.9.78.x):
    * Indicator classes are exposed through the *lowercase* submodule
      ``backtrader.indicators``.  The template's ``bt.Indicators.SMA`` /
      ``bt.Indicators.Cross`` are NOT valid in this build (``bt.Indicators``
      is undefined and there is no ``Cross`` class); use ``bt.indicators``
      and ``bt.indicators.CrossOver`` instead.
    * ``self.position`` is a ``PositionInfo`` object obtained as an attribute,
      not a ``bt.PositionInfo()`` class construction.
    * Broker order methods are ``self.buy()/sell()/short()/cover()``.  Helper
      methods therefore avoid these exact names to prevent self-recursion.
"""

import backtrader as bt


class DualMaStrategy(bt.Strategy):
    """Buy on a fast-over-slow golden cross, exit on a dead cross."""

    params = (
        ("fast", 10),        # period of the fast (short) moving average
        ("slow", 20),        # period of the slow (long) moving average
        # Order sizing (long-only): each "unit" is a slice of equity.
        ("order_pct", 0.01),  # fraction of equity per unit
        ("max_units", 10),   # max units per entry
        ("min_units", 1),    # min units per entry
        ("verbose", False),  # log signals
    )

    # ------------------------------------------------------------------ #
    #  Indicator setup                                                    #
    # ------------------------------------------------------------------ #
    def __init__(self):
        price = self.datas[0].close
        fast = bt.indicators.SMA(price, period=self.p.fast)
        slow = bt.indicators.SMA(price, period=self.p.slow)

        # CrossOver(data0, data1):
        #   +1.0 when data0 crosses data1 upwards
        #   -1.0 when data0 crosses data1 downwards
        #    0.0 otherwise
        # data0=fast, data1=slow  ->  bullish golden cross == +1.0
        self.sma_cross = bt.indicators.CrossOver(fast, slow)
        self.fast = fast
        self.slow = slow

    def log(self, txt):
        if self.p.verbose:
            dt = self.datas[0].dtdatetime[0]
            print("%s %s" % (str(dt), txt))

    # ------------------------------------------------------------------ #
    #  Order helpers (named to avoid shadowing broker buy/sell/short/cover)
    # ------------------------------------------------------------------ #
    def _units_for(self, fraction):
        """Integer number of shares equal to `fraction` of current equity."""
        equity = self.broker.getvalue()
        price = float(self.datas[0].close[0]) or 1e-9
        units = int(equity * fraction / price)
        return max(min(units, self.p.max_units), self.p.min_units)

    def _open_long(self):
        size = self._units_for(self.p.order_pct)
        if size > 0:
            self.log("BUY  size=%d" % size)
            self.buy(size=size)

    def _close_long(self):
        if self.position.size > 0:
            self.log("SELL size=%d" % self.position.size)
            self.sell(size=self.position.size)

    # ------------------------------------------------------------------ #
    #  Core loop                                                         #
    # ------------------------------------------------------------------ #
    def next(self):
        # 0.0 means "no cross"; +1/-1 mean a real signal.  backtrader already
        # skips the indicator warm-up bars, so the line is valid here.
        if not self.sma_cross[0]:
            return

        if self.sma_cross[0] > 0:
            # golden cross: buy only if flat (long-only)
            if self.position.size <= 0:
                self._open_long()
        else:  # < 0  -> death cross
            if self.position.size > 0:
                self._close_long()
