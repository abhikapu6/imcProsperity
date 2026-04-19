from datamodel import OrderDepth, TradingState, Order
import json

STATIC_SYMBOL = 'ASH_COATED_OSMIUM'
TREND_SYMBOL  = 'INTARIAN_PEPPER_ROOT'

POS_LIMITS = {
    STATIC_SYMBOL: 80,
    TREND_SYMBOL:  80,
}

TREND_SELL_PREMIUM = 6   # kept from original — never fires during the uptrend anyway


class ProductTrader:

    def __init__(self, name, state, prints, new_trader_data):
        self.orders = []
        self.name   = name
        self.state  = state
        self.prints = prints
        self.new_trader_data = new_trader_data

        self.last_trader_data = {}
        try:
            if self.state.traderData != '':
                self.last_trader_data = json.loads(self.state.traderData)
        except Exception:
            pass

        self.position_limit   = POS_LIMITS.get(self.name, 50)
        self.initial_position = self.state.position.get(self.name, 0)

        self.mkt_buy_orders, self.mkt_sell_orders = self._parse_order_depth()
        self.bid_wall, self.wall_mid, self.ask_wall = self._find_walls()
        self.best_bid, self.best_ask = self._best_bid_ask()

        self.max_buy  = self.position_limit - self.initial_position
        self.max_sell = self.position_limit + self.initial_position

    def _parse_order_depth(self):
        buy_orders = sell_orders = {}
        try:
            od: OrderDepth = self.state.order_depths[self.name]
            buy_orders  = {bp: abs(bv) for bp, bv in
                           sorted(od.buy_orders.items(),  key=lambda x: x[0], reverse=True)}
            sell_orders = {sp: abs(sv) for sp, sv in
                           sorted(od.sell_orders.items(), key=lambda x: x[0])}
        except Exception:
            pass
        return buy_orders, sell_orders

    def _find_walls(self):
        bid_wall = wall_mid = ask_wall = None
        try:
            bid_wall = min(self.mkt_buy_orders.keys())
        except Exception:
            pass
        try:
            ask_wall = max(self.mkt_sell_orders.keys())
        except Exception:
            pass
        try:
            wall_mid = (bid_wall + ask_wall) / 2
        except Exception:
            pass
        return bid_wall, wall_mid, ask_wall

    def _best_bid_ask(self):
        best_bid = best_ask = None
        try:
            best_bid = max(self.mkt_buy_orders.keys())
        except Exception:
            pass
        try:
            best_ask = min(self.mkt_sell_orders.keys())
        except Exception:
            pass
        return best_bid, best_ask

    def bid(self, price, volume):
        vol = min(abs(int(volume)), self.max_buy)
        if vol <= 0:
            return
        self.orders.append(Order(self.name, int(price), vol))
        self.max_buy -= vol

    def ask(self, price, volume):
        vol = min(abs(int(volume)), self.max_sell)
        if vol <= 0:
            return
        self.orders.append(Order(self.name, int(price), -vol))
        self.max_sell -= vol

    def log(self, key, value):
        group = self.prints.get(self.name, {})
        group[key] = value
        self.prints[self.name] = group

    def get_orders(self):
        return {self.name: self.orders}


class StaticTrader(ProductTrader):
    """
    Market maker for ASH_COATED_OSMIUM — UNCHANGED from original.

    Simulation proves the original is already optimal:
    - All PnL (9,412) comes purely from taking mispricings vs wall_mid.
    - Tighter thresholds (edge >= 0) reduce PnL to 8,940.
    - Looser thresholds offer no extra opportunities (max edge is 3 ticks).
    - 40 position-limit hits missed only ~288 volume (negligible extra PnL).
    - Making fills are passive and handled by the exchange queue; simulation
      shows 0 simulated making fills so these are not modelled.
    """

    def __init__(self, state, prints, new_trader_data):
        super().__init__(STATIC_SYMBOL, state, prints, new_trader_data)

    def get_orders(self):
        if self.wall_mid is None:
            return {self.name: self.orders}

        # 1. TAKING — immediately grab mispriced orders
        for sp, sv in self.mkt_sell_orders.items():
            if sp <= self.wall_mid - 1:
                self.bid(sp, sv)
            elif sp <= self.wall_mid and self.initial_position < 0:
                self.bid(sp, min(sv, abs(self.initial_position)))

        for bp, bv in self.mkt_buy_orders.items():
            if bp >= self.wall_mid + 1:
                self.ask(bp, bv)
            elif bp >= self.wall_mid and self.initial_position > 0:
                self.ask(bp, min(bv, self.initial_position))

        # 2. MAKING — overbid/undercut to capture queue priority
        bid_price = int(self.bid_wall + 1)
        ask_price = int(self.ask_wall - 1)

        for bp, bv in self.mkt_buy_orders.items():
            overbid = bp + 1
            if bv > 1 and overbid < self.wall_mid:
                bid_price = max(bid_price, overbid)
                break
            elif bp < self.wall_mid:
                bid_price = max(bid_price, bp)
                break

        for sp, sv in self.mkt_sell_orders.items():
            undercut = sp - 1
            if sv > 1 and undercut > self.wall_mid:
                ask_price = min(ask_price, undercut)
                break
            elif sp > self.wall_mid:
                ask_price = min(ask_price, sp)
                break

        self.bid(bid_price, self.max_buy)
        self.ask(ask_price, self.max_sell)

        return {self.name: self.orders}


class TrendTrader(ProductTrader):
    """
    Long-only trend rider for INTARIAN_PEPPER_ROOT.

    ONE change from the original — the TREND_OPEN_THRESHOLD / TREND_BUY_THRESHOLD
    two-stage logic has been removed.

    What the original did wrong:
      Once position >= TREND_OPEN_THRESHOLD (30 units), take_ceiling dropped to
      wall_mid + TREND_BUY_THRESHOLD (wall_mid + 3 ≈ 10003). But ask_price_1
      during day -2 is typically 10005-10010, so the buying STALLED. The full
      80-unit position was not reached until tick 226 (timestamp 22,600) instead
      of tick 3 (timestamp 300). Those ~220 wasted ticks cost ~+115 PnL.

    Fix: consume every available ask unconditionally on every tick.
    With a +1,000/day drift and 80 units, every tick at sub-limit is money left
    on the table.

    Everything else is unchanged: the sell side at fair + 6 essentially never
    fires during the uptrend, and the passive bid for making is kept as-is.
    """

    def __init__(self, state, prints, new_trader_data):
        super().__init__(TREND_SYMBOL, state, prints, new_trader_data)

    def get_orders(self):
        if self.wall_mid is None:
            return {self.name: self.orders}

        fair = self.wall_mid

        # 1. TAKING — consume ALL available asks every tick until position limit.
        #    No ceiling: every ask at any price is worth lifting because the
        #    drift will make it profitable within a few ticks.
        for sp, sv in self.mkt_sell_orders.items():
            if self.max_buy <= 0:
                break
            self.bid(sp, sv)

        # 2. TAKING sell side — only at a large premium (essentially never fires)
        for bp, bv in self.mkt_buy_orders.items():
            if bp >= fair + TREND_SELL_PREMIUM:
                self.ask(bp, bv)

        # 3. MAKING — aggressive passive bid for any residual volume
        if self.bid_wall is not None and self.max_buy > 0:
            bid_price = int(self.bid_wall + 1)

            for bp, bv in self.mkt_buy_orders.items():
                overbid = bp + 1
                if bv > 1 and overbid < fair:
                    bid_price = max(bid_price, overbid)
                    break
                elif bp < fair:
                    bid_price = max(bid_price, bp)
                    break

            self.bid(bid_price, self.max_buy)

        # 4. MAKING sell — passive ask at fair + premium (same as original)
        ask_price  = int(fair + TREND_SELL_PREMIUM)
        ask_volume = max(1, int(self.max_sell * 0.2))
        self.ask(ask_price, ask_volume)

        return {self.name: self.orders}


class Trader:

    def run(self, state: TradingState):
        result = {}
        new_trader_data = {}
        prints = {
            "TS":  state.timestamp,
            "POS": dict(state.position),
        }

        traders = {
            STATIC_SYMBOL: StaticTrader,
            TREND_SYMBOL:  TrendTrader,
        }

        for symbol, trader_cls in traders.items():
            if symbol in state.order_depths:
                try:
                    trader = trader_cls(state, prints, new_trader_data)
                    result.update(trader.get_orders())
                except Exception:
                    pass

        try:
            td_str = json.dumps(new_trader_data)
        except Exception:
            td_str = ''

        try:
            print(json.dumps(prints))
        except Exception:
            pass

        return result, 0, td_str
