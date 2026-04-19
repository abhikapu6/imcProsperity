from datamodel import OrderDepth, TradingState, Order
import json

STATIC_SYMBOL = 'ASH_COATED_OSMIUM'
TREND_SYMBOL = 'INTARIAN_PEPPER_ROOT'

POS_LIMITS = {
    STATIC_SYMBOL: 80,
    TREND_SYMBOL: 80,
}

TREND_SELL_PREMIUM = 6
TREND_BUY_THRESHOLD = 3
TREND_OPEN_THRESHOLD = 60
MAF_BID = 7421

class ProductTrader:
    def __init__(self, name, state, prints, new_trader_data):
        self.orders = []
        self.name = name
        self.state = state
        self.prints = prints
        self.new_trader_data = new_trader_data
        self.last_trader_data = {}
        try:
            if self.state.traderData != '':
                self.last_trader_data = json.loads(self.state.traderData)
        except:
            pass
        self.position_limit = POS_LIMITS.get(self.name, 50)
        self.initial_position = self.state.position.get(self.name, 0)
        self.mkt_buy_orders, self.mkt_sell_orders = self._parse_order_depth()
        self.bid_wall, self.wall_mid, self.ask_wall = self._find_walls()
        self.best_bid, self.best_ask = self._best_bid_ask()
        self.max_buy = self.position_limit - self.initial_position
        self.max_sell = self.position_limit + self.initial_position

    def _parse_order_depth(self):
        buy_orders, sell_orders = {}, {}
        try:
            od: OrderDepth = self.state.order_depths[self.name]
            buy_orders = {bp: abs(bv) for bp, bv in sorted(od.buy_orders.items(), key=lambda x: x[0], reverse=True)}
            sell_orders = {sp: abs(sv) for sp, sv in sorted(od.sell_orders.items(), key=lambda x: x[0])}
        except:
            pass
        return buy_orders, sell_orders

    def _find_walls(self):
        bid_wall = wall_mid = ask_wall = None
        try:
            bid_wall = min(self.mkt_buy_orders.keys())
        except:
            pass
        try:
            ask_wall = max(self.mkt_sell_orders.keys())
        except:
            pass
        try:
            wall_mid = (bid_wall + ask_wall) / 2
        except:
            pass
        return bid_wall, wall_mid, ask_wall

    def _best_bid_ask(self):
        best_bid = best_ask = None
        try:
            if self.mkt_buy_orders:
                best_bid = max(self.mkt_buy_orders.keys())
        except:
            pass
        try:
            if self.mkt_sell_orders:
                best_ask = min(self.mkt_sell_orders.keys())
        except:
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
    def __init__(self, state, prints, new_trader_data):
        super().__init__(STATIC_SYMBOL, state, prints, new_trader_data)

    def get_orders(self):
        if self.wall_mid is None:
            return {self.name: self.orders}

        fair = self.wall_mid - (self.initial_position * 0.05)

        for sp, sv in self.mkt_sell_orders.items():
            if sp <= fair - 1:
                self.bid(sp, sv)
            elif sp <= fair and self.initial_position < 0:
                self.bid(sp, min(sv, abs(self.initial_position)))

        for bp, bv in self.mkt_buy_orders.items():
            if bp >= fair + 1:
                self.ask(bp, bv)
            elif bp >= fair and self.initial_position > 0:
                self.ask(bp, min(bv, self.initial_position))

        bid_price = int(self.bid_wall + 1) if self.bid_wall else int(fair - 1)
        ask_price = int(self.ask_wall - 1) if self.ask_wall else int(fair + 1)

        for bp, bv in self.mkt_buy_orders.items():
            overbid = bp + 1
            if bv > 1 and overbid < fair:
                bid_price = max(bid_price, overbid)
                break
            elif bp < fair:
                bid_price = max(bid_price, bp)
                break

        for sp, sv in self.mkt_sell_orders.items():
            undercut = sp - 1
            if sv > 1 and undercut > fair:
                ask_price = min(ask_price, undercut)
                break
            elif sp > fair:
                ask_price = min(ask_price, sp)
                break

        self.bid(bid_price, self.max_buy)
        self.ask(ask_price, self.max_sell)

        return {self.name: self.orders}

class TrendTrader(ProductTrader):
    def __init__(self, state, prints, new_trader_data):
        super().__init__(TREND_SYMBOL, state, prints, new_trader_data)

    def get_orders(self):
        if self.wall_mid is None:
            return {self.name: self.orders}

        fair = self.wall_mid
        take_ceiling = (self.ask_wall if self.ask_wall is not None
                        and self.initial_position < TREND_OPEN_THRESHOLD
                        else fair + TREND_BUY_THRESHOLD)

        for sp, sv in self.mkt_sell_orders.items():
            if sp <= take_ceiling:
                self.bid(sp, sv)

        for bp, bv in self.mkt_buy_orders.items():
            if bp >= fair + TREND_SELL_PREMIUM:
                self.ask(bp, bv)

        bid_price = int(self.bid_wall + 1) if self.bid_wall is not None else int(fair - 1)

        for bp, bv in self.mkt_buy_orders.items():
            overbid = bp + 1
            if bv > 1 and overbid < fair:
                bid_price = max(bid_price, overbid)
                break
            elif bp < fair:
                bid_price = max(bid_price, bp)
                break

        self.bid(bid_price, self.max_buy)

        ask_price = int(fair + TREND_SELL_PREMIUM)
        ask_volume = max(1, int(self.max_sell * 0.2))
        self.ask(ask_price, ask_volume)

        return {self.name: self.orders}

class Trader:
    def bid(self):
        return MAF_BID

    def run(self, state: TradingState):
        result = {}
        new_trader_data = {}
        prints = {
            "TS": state.timestamp,
            "POS": dict(state.position),
        }

        traders = {
            STATIC_SYMBOL: StaticTrader,
            TREND_SYMBOL: TrendTrader,
        }

        for symbol, trader_cls in traders.items():
            if symbol in state.order_depths:
                try:
                    trader = trader_cls(state, prints, new_trader_data)
                    result.update(trader.get_orders())
                except:
                    pass

        try:
            td_str = json.dumps(new_trader_data)
        except:
            td_str = ''

        try:
            print(json.dumps(prints))
        except:
            pass

        return result, 0, td_str