Standalone Bybit Market Making Bot - Avellaneda-Stoikov Strategy

No external config files needed - everything is hardcoded for simplicity

INSTRUCTIONS:
1. Edit the API_KEY and API_SECRET below with your Bybit credentials
2. Adjust trading parameters if needed (or leave defaults)
3. Run: python standalone_market_maker.py
"""

import ccxt
import time
import math
import os
import sys
from datetime import datetime
from collections import deque
import statistics
import logging
from typing import Dict, Tuple, Optional, Any

# ============================================================================
# CONFIGURATION - EDIT THESE VALUES
# ============================================================================

# Bybit API Credentials (REQUIRED - Get from https://www.bybit.com/)
API_KEY = “xxxxxxxxxxxxxxx”  # Replace with your actual API key
API_SECRET = “xxxxxxxxxxxxxxxxx”  # Replace with your actual API secret
SANDBOX_MODE = False  # Set to True for testnet

# Trading Configuration
SYMBOL = "ETH/USDT:USDT"  # ETH perpetual futures
LEVERAGE = 20  # Leverage multiplier
ORDER_SIZE_FIXED = 0.01  # Fixed order size in ETH (matches server)
ORDER_SIZE_PERCENT = 0.02  # Fallback percentage if fixed size fails

# ATR Volatility Filter (NEW)
ATR_THRESHOLD = 22.0  # Minimum ATR value required to place orders (user configurable)
ATR_TIMEFRAME = '15m'  # Timeframe for ATR calculation
ATR_PERIOD = 14  # ATR calculation period (standard)
ENABLE_ATR_FILTER = True  # Enable/disable ATR filtering

# Avellaneda-Stoikov Parameters (Exact Server Match)
GAMMA = 0.01  # Risk aversion parameter (γ) - Controls spread width
K = 5.0  # Market impact parameter (k) - For fill intensity modeling  
ALPHA = 0.001  # Inventory penalty parameter (α) - Separate from gamma
TIME_HORIZON = 0.1  # Time horizon in hours (6 minutes) - rolling calculation
SIGMA_LOOKBACK = 50  # Price history length for volatility (matches server)
UPDATE_FREQUENCY = 1.0  # Update quotes every 1 second (ultra aggressive)
# Risk Management (Server-tuned)
MAX_INVENTORY_USD = 600  # Maximum inventory in USD

# Position Management (NEW)
PROFIT_THRESHOLD_SIZE = 0.09  # Inventory threshold for full position closing
ENABLE_PROFIT_PROTECTION = True  # Only close positions in profit
MIN_PROFIT_BPS = 120.0  # Minimum profit in basis points before closing

# ============================================================================
# BOT CODE - NO NEED TO EDIT BELOW THIS LINE
# ============================================================================

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Back to INFO from DEBUG
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('market_maker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class StandaloneMarketMaker:
    """Standalone Bybit market maker with hardcoded configuration"""
    
    def __init__(self):
        """Initialize the market maker with hardcoded configuration"""
        self.exchange = None
        self.symbol = SYMBOL
        self.price_history = deque(maxlen=SIGMA_LOOKBACK)
        self.inventory = 0
        self.pnl = 0
        self.trades_count = 0
        self.current_orders = {'bid': None, 'ask': None}
        self.volatility = 0.01
        self.running = False
        self.last_trade_check = 0
        
        # Position tracking (NEW)
        self.avg_buy_price = 0.0  # Average price of buy positions
        self.avg_sell_price = 0.0  # Average price of sell positions
        self.buy_quantity = 0.0  # Total quantity bought
        self.sell_quantity = 0.0  # Total quantity sold
        self.realized_pnl = 0.0  # Realized profit/loss
        
        # ATR filtering (NEW)
        self.current_atr = 0.0  # Current ATR value
        self.atr_history = deque(maxlen=100)  # ATR history for analysis
        self.last_atr_update = 0  # Last ATR update timestamp
        
        # Timing - use strategy start time instead of wall clock (matches server)
        self.start_time = time.time()
        
        # Validate configuration
        self._validate_config()
        
    def _validate_config(self) -> None:
        """Validate the hardcoded configuration"""
        if not API_KEY or API_KEY == "your_bybit_api_key" or "your_actual" in API_KEY:
            logger.error("Please set your actual Bybit API_KEY in the configuration section")
            sys.exit(1)
            
        if not API_SECRET or API_SECRET == "your_bybit_api_secret" or "your_actual" in API_SECRET:
            logger.error("Please set your actual Bybit API_SECRET in the configuration section")
            sys.exit(1)
            
        logger.info("Configuration validated successfully")
    
    def calculate_true_range(self, high: float, low: float, prev_close: float) -> float:
        """Calculate True Range for a single period"""
        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)
        return max(tr1, tr2, tr3)
    
    def calculate_atr(self, ohlcv_data: list) -> float:
        """Calculate ATR from OHLCV data"""
        if len(ohlcv_data) < ATR_PERIOD + 1:
            return self.current_atr or 0.0
        
        true_ranges = []
        
        # Calculate True Range for each period
        for i in range(1, len(ohlcv_data)):
            high = ohlcv_data[i][2]  # High price
            low = ohlcv_data[i][3]   # Low price
            prev_close = ohlcv_data[i-1][4]  # Previous close
            
            tr = self.calculate_true_range(high, low, prev_close)
            true_ranges.append(tr)
        
        # Calculate ATR as simple moving average of True Ranges
        if len(true_ranges) >= ATR_PERIOD:
            recent_trs = true_ranges[-ATR_PERIOD:]
            atr = sum(recent_trs) / len(recent_trs)
            return atr
        
        return self.current_atr or 0.0
    
    def fetch_atr_data(self) -> float:
        """Fetch 15-minute OHLCV data and calculate ATR"""
        try:
            # Update ATR every 30 seconds to avoid excessive API calls
            current_time = time.time()
            if current_time - self.last_atr_update < 30:
                return self.current_atr
            
            # Fetch 15-minute OHLCV data
            limit = ATR_PERIOD + 20  # Get extra data for stability
            ohlcv = self.exchange.fetch_ohlcv(
                self.symbol, 
                ATR_TIMEFRAME, 
                limit=limit
            )
            
            if len(ohlcv) >= ATR_PERIOD + 1:
                atr = self.calculate_atr(ohlcv)
                self.current_atr = atr
                self.atr_history.append(atr)
                self.last_atr_update = current_time
                
                logger.debug(f"ATR updated: {atr:.2f} (threshold: {ATR_THRESHOLD})")
                return atr
            else:
                logger.warning(f"Insufficient OHLCV data for ATR: {len(ohlcv)} bars")
                return self.current_atr
                
        except Exception as e:
            logger.error(f"Error fetching ATR data: {e}")
            return self.current_atr
    
    def should_place_orders_atr(self) -> bool:
        """Check if ATR conditions allow order placement"""
        if not ENABLE_ATR_FILTER:
            return True
        
        current_atr = self.fetch_atr_data()
        
        if current_atr >= ATR_THRESHOLD:
            return True
        else:
            logger.info(f"ATR FILTER: Orders blocked - ATR {current_atr:.2f} < threshold {ATR_THRESHOLD}")
            return False
    
    def initialize_exchange(self) -> None:
        """Initialize the Bybit exchange connection"""
        exchange_config = {
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'  # For perpetual contracts
            }
        }
        
        # Set sandbox mode if enabled
        if SANDBOX_MODE:
            exchange_config['sandbox'] = True
            logger.info("Running in SANDBOX/TESTNET mode")
        
        # Initialize Bybit exchange
        self.exchange = ccxt.bybit(exchange_config)
        
        # Load markets
        try:
            self.exchange.load_markets()
            logger.info("Successfully connected to Bybit")
        except Exception as e:
            logger.error(f"Failed to connect to Bybit: {e}")
            raise
    
    def validate_symbol(self) -> None:
        """Validate and set the trading symbol"""
        if self.symbol not in self.exchange.markets:
            available_symbols = [s for s in self.exchange.markets.keys() if 'USDT' in s]
            logger.error(f"Symbol {self.symbol} not found. Available symbols: {available_symbols[:10]}...")
            raise ValueError(f"Invalid symbol: {self.symbol}")
        
        market = self.exchange.markets[self.symbol]
        logger.info(f"Trading symbol: {self.symbol}")
        logger.info(f"Min order size: {market['limits']['amount']['min']}")
        logger.info(f"Price precision: {market['precision']['price']}")
    
    def set_leverage(self) -> None:
        """Set leverage for the trading pair"""
        try:
            if hasattr(self.exchange, 'set_leverage'):
                self.exchange.set_leverage(LEVERAGE, self.symbol)
                logger.info(f"Leverage set to {LEVERAGE}x")
        except Exception as e:
            logger.warning(f"Could not set leverage: {e}")
    
    def calculate_volatility(self) -> float:
        """Calculate realized volatility from price history"""
        if len(self.price_history) < 2:
            return self.volatility
        
        returns = []
        for i in range(1, len(self.price_history)):
            ret = math.log(self.price_history[i] / self.price_history[i-1])
            returns.append(ret)
        
        if len(returns) > 1:
            self.volatility = statistics.stdev(returns) * math.sqrt(3600)
            self.volatility = max(self.volatility, 0.001)
        
        return self.volatility
    
    def calculate_reservation_price(self, mid_price: float) -> float:
        """Calculate reservation price with proper inventory penalty (matches server)"""
        sigma = self.calculate_volatility()
        time_remaining = self.get_time_remaining()
        
        # Correct A-S formula: r = m - α * q * σ² * T
        # Note: Using alpha (inventory penalty), not gamma (risk aversion)
        inventory_penalty = ALPHA * self.inventory * sigma**2 * time_remaining
        reservation_price = mid_price - inventory_penalty
        
        return reservation_price
    
    def get_time_remaining(self) -> float:
        """Get time remaining in current strategy horizon (matches server)"""
        # Use rolling time horizon from strategy start, not wall clock
        elapsed_hours = (time.time() - self.start_time) / 3600
        cycle_position = elapsed_hours % TIME_HORIZON
        time_remaining = TIME_HORIZON - cycle_position
        return max(time_remaining, 0.01)  # Minimum time remaining
    
    def update_position_tracking(self, side: str, quantity: float, price: float) -> None:
        """Update position tracking with average entry prices"""
        if side == 'buy':
            # Update buy position tracking
            total_cost = self.avg_buy_price * self.buy_quantity + price * quantity
            self.buy_quantity += quantity
            self.avg_buy_price = total_cost / self.buy_quantity if self.buy_quantity > 0 else 0
            
        elif side == 'sell':
            # Update sell position tracking
            total_cost = self.avg_sell_price * self.sell_quantity + price * quantity
            self.sell_quantity += quantity
            self.avg_sell_price = total_cost / self.sell_quantity if self.sell_quantity > 0 else 0
        
        # Calculate realized PnL when positions offset
        if self.buy_quantity > 0 and self.sell_quantity > 0:
            closed_quantity = min(self.buy_quantity, self.sell_quantity)
            if side == 'sell' and self.avg_buy_price > 0:
                # Closing buy position with sell
                self.realized_pnl += closed_quantity * (price - self.avg_buy_price)
            elif side == 'buy' and self.avg_sell_price > 0:
                # Closing sell position with buy
                self.realized_pnl += closed_quantity * (self.avg_sell_price - price)
            
            # Reduce quantities by closed amount
            self.buy_quantity -= closed_quantity
            self.sell_quantity -= closed_quantity
            
            # Reset average prices if position is fully closed
            if self.buy_quantity == 0:
                self.avg_buy_price = 0.0
            if self.sell_quantity == 0:
                self.avg_sell_price = 0.0
    
    def get_net_position_info(self) -> Tuple[float, float, bool]:
        """Get current net position info: (net_quantity, avg_price, is_profitable)"""
        net_quantity = self.buy_quantity - self.sell_quantity
        
        if net_quantity > 0:
            # Long position (net buys)
            avg_price = self.avg_buy_price
            is_profitable = False  # Will be calculated against current ask
        elif net_quantity < 0:
            # Short position (net sells)
            avg_price = self.avg_sell_price
            is_profitable = False  # Will be calculated against current bid
        else:
            # No position
            avg_price = 0.0
            is_profitable = False
        
        return net_quantity, avg_price, is_profitable
    
    def fetch_exchange_position(self) -> Tuple[float, float]:
        """Fetch actual position from exchange for verification"""
        try:
            # Get position from exchange
            positions = self.exchange.fetch_positions([self.symbol])
            
            for pos in positions:
                if pos['symbol'] == self.symbol:
                    # Try different ways to get position size
                    size = 0.0
                    if pos.get('contracts') and pos['contracts'] != 0:
                        size = float(pos['contracts'])
                    elif pos.get('size') and pos['size'] != 0:
                        size = float(pos['size'])
                    elif pos.get('amount') and pos['amount'] != 0:
                        size = float(pos['amount'])
                    
                    # Try different ways to get entry price
                    entry_price = 0.0
                    if pos.get('entryPrice') and pos['entryPrice'] != 0:
                        entry_price = float(pos['entryPrice'])
                    elif pos.get('avgPrice') and pos['avgPrice'] != 0:
                        entry_price = float(pos['avgPrice'])
                    elif pos.get('markPrice') and pos['markPrice'] != 0:
                        entry_price = float(pos['markPrice'])
                    
                    # Handle position direction
                    if pos.get('side') == 'short':
                        size = -size
                    
                    if size != 0:
                        return size, entry_price
            
            return 0.0, 0.0
            
        except Exception as e:
            logger.error(f"Error fetching exchange position: {e}")
            return 0.0, 0.0
    
    def sync_position_with_exchange(self) -> None:
        """Synchronize internal position tracking with exchange"""
        try:
            exchange_size, exchange_price = self.fetch_exchange_position()
            
            # If we have a significant discrepancy, auto-correct it
            internal_net = self.buy_quantity - self.sell_quantity
            if abs(exchange_size - internal_net) > 0.001:  # More than 0.001 ETH difference
                logger.warning(f"Position sync mismatch: Internal={internal_net:.4f}, Exchange={exchange_size:.4f}")
                
                # AUTO-CORRECT: Sync internal tracking with exchange position
                if exchange_size != 0 and exchange_price > 0:
                    logger.info(f"Auto-correcting internal position to match exchange")
                    
                    # Reset internal tracking
                    self.buy_quantity = 0.0
                    self.sell_quantity = 0.0 
                    self.avg_buy_price = 0.0
                    self.avg_sell_price = 0.0
                    
                    # Set position based on exchange data
                    if exchange_size > 0:  # Long position
                        self.buy_quantity = exchange_size
                        self.avg_buy_price = exchange_price
                        logger.info(f"Set internal LONG position: {self.buy_quantity:.4f} ETH @ ${self.avg_buy_price:.4f}")
                    elif exchange_size < 0:  # Short position  
                        self.sell_quantity = abs(exchange_size)
                        self.avg_sell_price = exchange_price
                        logger.info(f"Set internal SHORT position: {self.sell_quantity:.4f} ETH @ ${self.avg_sell_price:.4f}")
                    
                    # Update inventory to match
                    self.inventory = exchange_size
                    logger.info(f"Updated inventory to match: {self.inventory:.4f} ETH")
                else:
                    logger.info(f"Exchange position: {exchange_size:.4f} ETH @ ${exchange_price:.2f}")
        except Exception as e:
            logger.debug(f"Position sync error: {e}")
    
    def calculate_optimal_spread(self, mid_price: float) -> float:
        """Calculate optimal bid-ask spread using Avellaneda-Stoikov (matches server)"""
        sigma = self.calculate_volatility()
        time_remaining = self.get_time_remaining()
        
        # Correct A-S optimal spread: δ* = γσ²T + (2/γ)ln(1 + γ/k)
        risk_term = GAMMA * sigma**2 * time_remaining
        market_impact_term = (2 / GAMMA) * math.log(1 + GAMMA / K)
        
        spread = risk_term + market_impact_term
        
        # Server's constraints: minimum spread (wider than before)
        min_spread_bps = 2.0  # 2 basis points minimum
        min_spread = (min_spread_bps / 10000) * mid_price
        spread = max(spread * mid_price, min_spread)
        
        # Server's maximum spread constraint (CRITICAL!)
        max_spread_bps = 20.0  # 20 basis points maximum
        max_spread = (max_spread_bps / 10000) * mid_price
        spread = min(spread, max_spread)
        
        return spread
    
    def calculate_quote_prices(self, mid_price: float) -> Tuple[float, float]:
        """Calculate optimal bid and ask prices (matches server exactly)"""
        reservation_price = self.calculate_reservation_price(mid_price)
        spread = self.calculate_optimal_spread(mid_price)
        
        # Calculate base bid and ask around reservation price
        bid_price = reservation_price - spread / 2
        ask_price = reservation_price + spread / 2
        
        # Server's exact quote distance constraint (CRITICAL!)
        min_spread_from_mid = mid_price * 0.0005  # 5 bps minimum from mid (not 10bps!)
        bid_price = min(bid_price, mid_price - min_spread_from_mid)
        ask_price = max(ask_price, mid_price + min_spread_from_mid)
        
        # PROFIT PROTECTION: Apply price floors/ceilings (NEW)
        if ENABLE_PROFIT_PROTECTION:
            net_quantity, avg_entry_price, _ = self.get_net_position_info()
            min_profit_amount = avg_entry_price * (MIN_PROFIT_BPS / 10000) if avg_entry_price > 0 else 0
            
            if net_quantity > 0:  # Long position (have buys)
                # Only allow asks (sells) above buy average + minimum profit
                profit_floor = avg_entry_price + min_profit_amount
                ask_price = max(ask_price, profit_floor)
                logger.debug(f"Long position protection: ask >= {profit_floor:.4f} (avg: {avg_entry_price:.4f})")
                
            elif net_quantity < 0:  # Short position (have sells)
                # Only allow bids (buys) below sell average - minimum profit
                profit_ceiling = avg_entry_price - min_profit_amount
                bid_price = min(bid_price, profit_ceiling)
                logger.debug(f"Short position protection: bid <= {profit_ceiling:.4f} (avg: {avg_entry_price:.4f})")
        
        # Round to exchange precision
        market = self.exchange.markets[self.symbol]
        price_precision = market['precision']['price']
        
        if isinstance(price_precision, int):
            bid_price = round(bid_price, price_precision)
            ask_price = round(ask_price, price_precision)
        else:
            # Handle tick size
            tick_size = float(price_precision)
            bid_price = round(bid_price / tick_size) * tick_size
            ask_price = round(ask_price / tick_size) * tick_size
        
        return bid_price, ask_price
    
    def calculate_position_size(self, price: float, order_side: str = None) -> float:
        """Calculate position size based on configuration and order side"""
        market = self.exchange.markets[self.symbol]
        min_size = market['limits']['amount']['min']
        
        # Get position information
        net_quantity, avg_entry_price, _ = self.get_net_position_info()
        abs_position = abs(net_quantity)
        
        # FULL POSITION CLOSING: Only when placing opposite-side orders (NEW)
        if abs_position >= PROFIT_THRESHOLD_SIZE and avg_entry_price > 0 and order_side:
            current_mid = price  # Use current price as approximation
            
            # Check if we should close entire position with this order side
            should_close_full = False
            
            if net_quantity > 0 and order_side == 'sell':  # Long position, placing sell order
                # Check if sell order would be profitable
                is_profitable = current_mid > avg_entry_price * (1 + MIN_PROFIT_BPS / 10000)
                should_close_full = is_profitable
                
            elif net_quantity < 0 and order_side == 'buy':  # Short position, placing buy order  
                # Check if buy order would be profitable
                is_profitable = current_mid < avg_entry_price * (1 - MIN_PROFIT_BPS / 10000)
                should_close_full = is_profitable
            
            if should_close_full:
                # Close entire position when profitable
                full_position_size = abs_position
                logger.info(f"PROFIT CLOSING: Full {order_side} order size {full_position_size:.4f} ETH to close entire position")
                
                # Round to exchange precision
                amount_precision = market['precision']['amount']
                if isinstance(amount_precision, int):
                    full_position_size = round(full_position_size, amount_precision)
                else:
                    lot_size = float(amount_precision)
                    full_position_size = round(full_position_size / lot_size) * lot_size
                
                return max(full_position_size, min_size)
        
        # Use fixed order size (matches server behavior)
        base_size = ORDER_SIZE_FIXED
        
        # Inventory adjustment - reduce size when inventory is high
        inventory_value = abs(self.inventory * price)
        
        if inventory_value > MAX_INVENTORY_USD * 0.7:
            size_multiplier = 0.5
        elif inventory_value > MAX_INVENTORY_USD * 0.5:
            size_multiplier = 0.75
        else:
            size_multiplier = 1.0
        
        size = base_size * size_multiplier
        
        # Round to exchange precision
        amount_precision = market['precision']['amount']
        if isinstance(amount_precision, int):
            size = round(size, amount_precision)
        else:
            # Handle lot size
            lot_size = float(amount_precision)
            size = round(size / lot_size) * lot_size
        
        return max(size, min_size)
    
    def get_available_balance(self) -> float:
        """Get available balance in USDT"""
        try:
            balance = self.exchange.fetch_balance()
            return balance.get('USDT', {}).get('free', 0)
        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            return 0
    
    def cancel_all_orders(self) -> None:
        """Cancel all open orders"""
        try:
            if hasattr(self.exchange, 'cancel_all_orders'):
                self.exchange.cancel_all_orders(self.symbol)
            else:
                open_orders = self.exchange.fetch_open_orders(self.symbol)
                for order in open_orders:
                    self.exchange.cancel_order(order['id'], self.symbol)
            
            self.current_orders = {'bid': None, 'ask': None}
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")
    
    def place_orders(self, bid_price: float, ask_price: float) -> None:
        """Place bid and ask orders"""
        # Check ATR filter first (NEW)
        if not self.should_place_orders_atr():
            logger.info("Orders blocked due to low ATR - maintaining current quotes")
            return
        
        self.cancel_all_orders()
        
        # Get position information to check if we're in closing mode
        net_quantity, avg_entry_price, _ = self.get_net_position_info()
        abs_position = abs(net_quantity)
        is_closing_mode = False
        closing_side = None
        
        # Check if we're in position closing mode
        if abs_position >= PROFIT_THRESHOLD_SIZE and avg_entry_price > 0:
            mid_price = (bid_price + ask_price) / 2  # Approximate mid price
            min_profit_amount = avg_entry_price * (MIN_PROFIT_BPS / 10000)
            
            if net_quantity > 0:  # Long position
                # Check if we can close profitably with a sell
                is_profitable_sell = mid_price > avg_entry_price * (1 + MIN_PROFIT_BPS / 10000)
                if is_profitable_sell:
                    is_closing_mode = True
                    closing_side = 'sell'
                    logger.info(f"CLOSING MODE: Will only place SELL orders to close LONG position")
                    
            elif net_quantity < 0:  # Short position
                # Check if we can close profitably with a buy
                is_profitable_buy = mid_price < avg_entry_price * (1 - MIN_PROFIT_BPS / 10000)
                if is_profitable_buy:
                    is_closing_mode = True
                    closing_side = 'buy'
                    logger.info(f"CLOSING MODE: Will only place BUY orders to close SHORT position")
        
        # Calculate separate sizes for bid and ask orders
        bid_size = self.calculate_position_size(bid_price, 'buy')
        ask_size = self.calculate_position_size(ask_price, 'sell')
        
        # In closing mode, only place orders on the closing side
        if is_closing_mode:
            if closing_side == 'buy':
                # Only place buy order to close short position
                try:
                    bid_order = self.exchange.create_limit_order(
                        self.symbol, 'buy', bid_size, bid_price
                    )
                    self.current_orders['bid'] = bid_order
                    logger.info(f"CLOSING ORDER - Bid placed: {bid_size} @ {bid_price}")
                except Exception as e:
                    logger.error(f"Error placing closing bid: {e}")
                    
            elif closing_side == 'sell':
                # Only place sell order to close long position
                try:
                    ask_order = self.exchange.create_limit_order(
                        self.symbol, 'sell', ask_size, ask_price
                    )
                    self.current_orders['ask'] = ask_order
                    logger.info(f"CLOSING ORDER - Ask placed: {ask_size} @ {ask_price}")
                except Exception as e:
                    logger.error(f"Error placing closing ask: {e}")
        else:
            # Normal market making mode - place both sides
            try:
                # Place bid order
                bid_order = self.exchange.create_limit_order(
                    self.symbol, 'buy', bid_size, bid_price
                )
                self.current_orders['bid'] = bid_order
                logger.info(f"Bid placed: {bid_size} @ {bid_price}")
            except Exception as e:
                logger.error(f"Error placing bid: {e}")
            
            try:
                # Place ask order
                ask_order = self.exchange.create_limit_order(
                    self.symbol, 'sell', ask_size, ask_price
                )
                self.current_orders['ask'] = ask_order
                logger.info(f"Ask placed: {ask_size} @ {ask_price}")
            except Exception as e:
                logger.error(f"Error placing ask: {e}")
    
    def update_inventory(self) -> None:
        """Update inventory from recent trades"""
        try:
            # Check trades more frequently for better position tracking (every 10 seconds instead of 30)
            current_time = time.time()
            if current_time - self.last_trade_check < 10:
                return
            
            self.last_trade_check = current_time
            since = int((current_time - 600) * 1000)  # Last 10 minutes (extended from 5)
            trades = self.exchange.fetch_my_trades(self.symbol, since=since, limit=100)  # More trades
            
            new_trades_found = 0
            for trade in trades:
                # Only process trades from the last check period
                if trade['timestamp'] > (current_time - 12) * 1000:  # Last 12 seconds
                    trade_side = trade['side']
                    trade_amount = trade['amount']
                    trade_price = trade['price']
                    
                    # Update inventory
                    if trade_side == 'buy':
                        self.inventory += trade_amount
                    else:
                        self.inventory -= trade_amount
                    
                    # Update position tracking (NEW)
                    self.update_position_tracking(trade_side, trade_amount, trade_price)
                    
                    self.trades_count += 1
                    new_trades_found += 1
                    
                    # Update PnL
                    fee = trade.get('fee', {}).get('cost', 0)
                    self.pnl -= fee
                    
                    logger.info(f"FILL: {trade_side.upper()} {trade_amount} @ ${trade_price}")
                    logger.info(f"Position: Buy={self.buy_quantity:.4f} @ ${self.avg_buy_price:.4f}, Sell={self.sell_quantity:.4f} @ ${self.avg_sell_price:.4f}, Net={self.buy_quantity - self.sell_quantity:.4f}")
            
            # Only log if there are no new trades occasionally to avoid spam
            if new_trades_found == 0 and current_time % 60 < 1:  # Log every minute
                logger.debug(f"Position status: Buy={self.buy_quantity:.4f}, Sell={self.sell_quantity:.4f}, Net={self.buy_quantity - self.sell_quantity:.4f}")
            
        except Exception as e:
            logger.error(f"Error updating inventory: {e}")
            
        # Also check for any missed fills by examining order status
        self.check_order_fills()
    
    def check_order_fills(self) -> None:
        """Check current orders for fills and update position accordingly"""
        try:
            # Check our current tracked orders
            for side, order in self.current_orders.items():
                if order and 'id' in order:
                    try:
                        # Fetch current order status
                        order_status = self.exchange.fetch_order(order['id'], self.symbol)
                        
                        if order_status['status'] == 'closed':
                            # Order was filled
                            filled_amount = order_status['filled']
                            avg_price = order_status['average'] or order_status['price']
                            order_side = order_status['side']
                            
                            logger.info(f"ORDER FILLED: {order_side.upper()} {filled_amount} @ ${avg_price}")
                            
                            # This fill should be caught by trade history, but just in case...
                            # We don't double-count here, just log for awareness
                            
                            # Clear the filled order from tracking
                            self.current_orders[side] = None
                            
                    except Exception as e:
                        # Order might not exist anymore, clear it
                        logger.debug(f"Could not check order {order['id']}: {e}")
                        self.current_orders[side] = None
                        
        except Exception as e:
            logger.debug(f"Error checking order fills: {e}")
    
    def display_status(self, mid_price: float, bid_price: float, ask_price: float, bid_size: float, ask_size: float) -> None:
        """Display current bot status"""
        spread = ask_price - bid_price
        spread_bps = (spread / mid_price) * 10000
        balance = self.get_available_balance()
        inventory_value = self.inventory * mid_price
        
        # Get position information (NEW)
        net_quantity, avg_entry_price, _ = self.get_net_position_info()
        net_value = net_quantity * mid_price
        
        # Calculate unrealized PnL (NEW)
        unrealized_pnl = 0.0
        if net_quantity != 0 and avg_entry_price > 0:
            if net_quantity > 0:  # Long position
                unrealized_pnl = net_quantity * (mid_price - avg_entry_price)
            else:  # Short position
                unrealized_pnl = abs(net_quantity) * (avg_entry_price - mid_price)
        
        # Use dynamic time remaining to match server
        time_remaining = self.get_time_remaining()
        
        print(f"\n{'='*80}")
        print(f"ETH: ${mid_price:.2f} | Spread: {spread_bps:.1f}bps | σ: {self.volatility:.3f} | T-rem: {time_remaining:.3f}h")
        
        # ATR Filter Status (NEW)
        atr_status = "ACTIVE" if ENABLE_ATR_FILTER else "DISABLED"
        atr_color = "✅" if self.current_atr >= ATR_THRESHOLD or not ENABLE_ATR_FILTER else "❌"
        print(f"ATR Filter: {atr_status} | Current: {self.current_atr:.2f} | Threshold: {ATR_THRESHOLD} {atr_color}")
        
        print(f"Inventory: {self.inventory:.3f} ETH (${inventory_value:.2f}) | Target: 0")
        
        # Position information (NEW)
        if net_quantity != 0:
            position_type = "LONG" if net_quantity > 0 else "SHORT"
            profit_symbol = "+" if unrealized_pnl > 0 else "-" if unrealized_pnl < 0 else "="
            print(f"Position: {position_type} {abs(net_quantity):.4f} ETH @ ${avg_entry_price:.2f} | Unrealized: {profit_symbol} ${unrealized_pnl:.2f}")
            
            # Show buy/sell position components
            if self.buy_quantity > 0:
                print(f"  Buys: {self.buy_quantity:.4f} ETH @ ${self.avg_buy_price:.2f}")
            if self.sell_quantity > 0:
                print(f"  Sells: {self.sell_quantity:.4f} ETH @ ${self.avg_sell_price:.2f}")
        else:
            print(f"Position: FLAT | No open positions")
        
        # Show separate bid/ask sizes (UPDATED)
        print(f"Quotes: ${bid_price:.2f} ({bid_size:.3f}) / ${ask_price:.2f} ({ask_size:.3f}) ETH")
        
        # Enhanced PnL display (NEW)
        total_pnl = self.realized_pnl + unrealized_pnl
        pnl_symbol = "+" if total_pnl > 0 else "-" if total_pnl < 0 else "="
        print(f"PnL: Realized ${self.realized_pnl:.2f} + Unrealized ${unrealized_pnl:.2f} = {pnl_symbol} ${total_pnl:.2f}")
        
        print(f"Stats: {self.trades_count} trades | Balance: ${balance:.2f} USDT")
        print(f"Params: k: {K:.2f} | γ: {GAMMA:.3f} | Profit Floor: {MIN_PROFIT_BPS:.1f}bps | ATR: {ATR_THRESHOLD}")
        
        # Risk indicators
        inventory_percent = abs(inventory_value) / MAX_INVENTORY_USD * 100
        if inventory_percent > 70:
            print(f"HIGH INVENTORY RISK: {inventory_percent:.1f}%")
        elif inventory_percent > 50:
            print(f"MEDIUM INVENTORY: {inventory_percent:.1f}%")
        else:
            print(f"INVENTORY OK: {inventory_percent:.1f}%")
        
        # Position size warning and closing mode status (UPDATED)
        if abs(net_quantity) >= PROFIT_THRESHOLD_SIZE:
            min_profit_amount = avg_entry_price * (MIN_PROFIT_BPS / 10000) if avg_entry_price > 0 else 0
            
            if net_quantity > 0:  # Long position
                target_price = avg_entry_price + min_profit_amount if avg_entry_price > 0 else 0
                is_profitable = mid_price > target_price if target_price > 0 else False
                mode_status = "🎯 CLOSING MODE ACTIVE" if is_profitable else "⏳ WAITING FOR PROFIT TARGET"
                print(f"LARGE LONG POSITION: {mode_status} | Target: ${target_price:.4f}")
                
            elif net_quantity < 0:  # Short position
                target_price = avg_entry_price - min_profit_amount if avg_entry_price > 0 else 0
                is_profitable = mid_price < target_price if target_price > 0 else False
                mode_status = "🎯 CLOSING MODE ACTIVE" if is_profitable else "⏳ WAITING FOR PROFIT TARGET"
                print(f"LARGE SHORT POSITION: {mode_status} | Target: ${target_price:.4f}")
        
        # Protection status (NEW)
        elif ENABLE_PROFIT_PROTECTION and avg_entry_price > 0:
            if net_quantity > 0:
                protection_price = avg_entry_price * (1 + MIN_PROFIT_BPS / 10000)
                print(f"LONG PROTECTION: Asks only above ${protection_price:.4f}")
            elif net_quantity < 0:
                protection_price = avg_entry_price * (1 - MIN_PROFIT_BPS / 10000)
                print(f"SHORT PROTECTION: Bids only below ${protection_price:.4f}")
    
    def run(self) -> None:
        """Main bot loop"""
        print("🚀 Starting Standalone Bybit Market Maker Bot")
        print(f"📋 Configuration:")
        print(f"   Symbol: {SYMBOL}")
        print(f"   Leverage: {LEVERAGE}x")
        print(f"   Order Size: {ORDER_SIZE_FIXED} ETH (fixed)")
        print(f"   Max Inventory: ${MAX_INVENTORY_USD}")
        print(f"   Update Frequency: {UPDATE_FREQUENCY}s")
        print(f"   Parameters: γ={GAMMA}, k={K}, T={TIME_HORIZON}h (rolling)")
        print(f"   Sandbox Mode: {SANDBOX_MODE}")
        print(f"📊 ATR Volatility Filter:")
        print(f"   Filter Enabled: {'Yes' if ENABLE_ATR_FILTER else 'No'}")
        print(f"   ATR Threshold: {ATR_THRESHOLD}")
        print(f"   ATR Timeframe: {ATR_TIMEFRAME}")
        print(f"   ATR Period: {ATR_PERIOD}")
        print(f"🛡️  Position Management:")
        print(f"   Profit Protection: {'Enabled' if ENABLE_PROFIT_PROTECTION else 'Disabled'}")
        print(f"   Minimum Profit: {MIN_PROFIT_BPS:.1f} basis points")
        print(f"   Full Close Threshold: {PROFIT_THRESHOLD_SIZE} ETH")
        
        # Initialize exchange
        self.initialize_exchange()
        self.validate_symbol()
        self.set_leverage()
        
        self.running = True
        
        logger.info("Bot started successfully!")
        logger.info(f"Position management: Protection={ENABLE_PROFIT_PROTECTION}, MinProfit={MIN_PROFIT_BPS}bps, Threshold={PROFIT_THRESHOLD_SIZE}")
        logger.info(f"ATR Filter: Enabled={ENABLE_ATR_FILTER}, Threshold={ATR_THRESHOLD}, Timeframe={ATR_TIMEFRAME}, Period={ATR_PERIOD}")
        
        while self.running:
            try:
                start_time = time.time()
                
                # Fetch orderbook
                orderbook = self.exchange.fetch_order_book(self.symbol)
                if not orderbook['bids'] or not orderbook['asks']:
                    logger.warning("Empty orderbook, retrying...")
                    time.sleep(1)
                    continue
                
                # Calculate mid price
                best_bid = orderbook['bids'][0][0]
                best_ask = orderbook['asks'][0][0]
                mid_price = (best_bid + best_ask) / 2
                
                # Update price history
                self.price_history.append(mid_price)
                
                # Update inventory
                self.update_inventory()
                
                # Sync position with exchange
                self.sync_position_with_exchange()
                
                # Check risk limits - MODIFIED TO ALLOW CLOSING PROFITABLE POSITIONS
                inventory_value = abs(self.inventory * mid_price)
                
                if inventory_value > MAX_INVENTORY_USD:
                    logger.warning(f"INVENTORY LIMIT REACHED: ${inventory_value:.2f} > ${MAX_INVENTORY_USD}")
                    
                    # Check if we can close a profitable position
                    net_quantity, avg_entry_price, _ = self.get_net_position_info()
                    can_close_profitably = False
                    
                    if abs(net_quantity) > 0 and avg_entry_price > 0:
                        min_profit_amount = avg_entry_price * (MIN_PROFIT_BPS / 10000)
                        
                        if net_quantity > 0:  # Long position - can we sell profitably?
                            min_sell_price = avg_entry_price + min_profit_amount
                            if mid_price > min_sell_price:
                                can_close_profitably = True
                                logger.info(f"INVENTORY LIMIT: Can close LONG position profitably (mid: ${mid_price:.4f} > min: ${min_sell_price:.4f})")
                                
                        elif net_quantity < 0:  # Short position - can we buy profitably?
                            max_buy_price = avg_entry_price - min_profit_amount
                            if mid_price < max_buy_price:
                                can_close_profitably = True
                                logger.info(f"INVENTORY LIMIT: Can close SHORT position profitably (mid: ${mid_price:.4f} < max: ${max_buy_price:.4f})")
                    
                    if can_close_profitably:
                        # Allow normal quoting logic to run, it will handle profit protection
                        logger.info("INVENTORY LIMIT: Allowing closing orders for profitable position")
                    else:
                        # No profitable position to close, skip this cycle
                        self.cancel_all_orders()
                        time.sleep(10)
                        continue
                
                # Calculate quotes
                bid_price, ask_price = self.calculate_quote_prices(mid_price)
                bid_size = self.calculate_position_size(bid_price, 'buy')
                ask_size = self.calculate_position_size(ask_price, 'sell')
                
                # Display status
                self.display_status(mid_price, bid_price, ask_price, bid_size, ask_size)
                
                # Place orders
                self.place_orders(bid_price, ask_price)
                
                # Sleep until next update
                elapsed = time.time() - start_time
                sleep_time = max(0, UPDATE_FREQUENCY - elapsed)
                time.sleep(sleep_time)
                
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(5)
        
        # Cleanup
        self.cancel_all_orders()
        logger.info("Bot stopped")
    
    def stop(self) -> None:
        """Stop the bot"""
        self.running = False


def main():
    """Main entry point"""
    print("=" * 70)
    print("🎯 STANDALONE BYBIT MARKET MAKER")
    print("📈 Avellaneda-Stoikov Strategy")
    print("⚡ Ultra High-Frequency Trading")
    print("=" * 70)

    # Create and run bot
    bot = StandaloneMarketMaker()
    
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"💥 Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main() 
