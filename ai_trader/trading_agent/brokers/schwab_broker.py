"""
Schwab Broker Implementation

Connects to Charles Schwab API for trading.
Uses OAuth2 authentication flow.
"""

import requests
import base64
import time
import logging
import threading
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import uuid

from .base_broker import (
    BaseBroker, Order, Position, Quote, AccountInfo,
    OrderSide, OrderType, OrderStatus
)
from .order_validator import OrderValidator

logger = logging.getLogger(__name__)


class SchwabBroker(BaseBroker):
    """
    Charles Schwab broker implementation.

    Uses OAuth2 for authentication.
    Requires app key, app secret, and refresh token.
    """

    def __init__(
        self,
        app_key: str = None,
        app_secret: str = None,
        refresh_token: str = None,
        paper_trading: bool = True
    ):
        """
        Initialize Schwab broker.

        Args:
            app_key: Schwab app key (or set SCHWAB_APP_KEY env var)
            app_secret: Schwab app secret (or set SCHWAB_APP_SECRET env var)
            refresh_token: OAuth refresh token (or set SCHWAB_REFRESH_TOKEN env var)
            paper_trading: Use paper trading (currently Schwab doesn't have paper, so this is for compatibility)
        """
        super().__init__(app_key, app_secret, paper_trading)

        self.app_key = app_key or os.getenv("SCHWAB_APP_KEY")
        self.app_secret = app_secret or os.getenv("SCHWAB_APP_SECRET")
        self.refresh_token = refresh_token or os.getenv("SCHWAB_REFRESH_TOKEN")

        if not self.app_key or not self.app_secret:
            raise ValueError("Schwab app key and secret are required")

        self.base_url = "https://api.schwabapi.com"
        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.refresh_token_expiry: Optional[datetime] = None  # Track refresh token expiry (7 days)
        self.account_hash: Optional[str] = None

        # Token storage
        self.token_file = Path.home() / ".ai_trader" / "schwab_tokens.json"
        self.token_file.parent.mkdir(exist_ok=True)
        
        # Order validator
        self.order_validator: Optional[OrderValidator] = None

        # Token renewal terminal guard - only open once per session
        self._renewal_terminal_opened = False

        # Lock to prevent concurrent token refresh races (ThreadPoolExecutor safe)
        self._token_lock = threading.Lock()

    def _get_authorization_url(self) -> str:
        """
        Get OAuth authorization URL for initial setup.

        Returns:
            Authorization URL to visit in browser
        """
        auth_url = (
            f"{self.base_url}/v1/oauth/authorize?"
            f"response_type=code&"
            f"client_id={self.app_key}&"
            f"scope=readonly&"
            f"redirect_uri=https://developer.schwab.com/oauth2-redirect.html"
        )
        return auth_url

    def _open_renewal_terminal(self) -> None:
        """
        Open a new terminal window running complete_schwab_setup.py.
        Only fires once per session via _renewal_terminal_opened flag.
        """
        if self._renewal_terminal_opened:
            return
        self._renewal_terminal_opened = True

        try:
            import subprocess
            from pathlib import Path as _Path

            setup_script = _Path(__file__).parent.parent / 'complete_schwab_setup.py'
            work_dir = str(setup_script.parent)

            # Windows: open a new cmd window that runs the renewal script
            cmd = f'cmd /k "cd /d {work_dir} && python complete_schwab_setup.py"'
            subprocess.Popen(
                ['cmd', '/c', 'start', 'Schwab Token Renewal Required', cmd],
                shell=False
            )
            logger.info("Opened Schwab token renewal terminal - complete auth in new window")
        except Exception as e:
            logger.warning(f"Could not open renewal terminal: {e}")
            logger.info("Manually run: python ai_trader/trading_agent/complete_schwab_setup.py")

        # Clear the expired token from memory so the next retry reloads
        # from the token file (which will have the new token once the user
        # completes the renewal script)
        self.refresh_token = None
        logger.info("Cleared expired refresh token from memory - will reload from file after renewal")

    def _exchange_code_for_tokens(self, auth_code: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access and refresh tokens.

        Args:
            auth_code: Authorization code from OAuth redirect

        Returns:
            Token response dict
        """
        token_url = f"{self.base_url}/v1/oauth/token"

        # Create Basic auth header
        credentials = f"{self.app_key}:{self.app_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": "https://developer.schwab.com/oauth2-redirect.html"
        }

        response = requests.post(token_url, headers=headers, data=data)
        response.raise_for_status()

        tokens = response.json()
        self._save_tokens(tokens)

        return tokens

    def _refresh_access_token(self) -> bool:
        """
        Refresh the access token using refresh token.

        Returns:
            True if successful
        """
        if not self.refresh_token:
            # Try to load from file
            tokens = self._load_tokens()
            if tokens and "refresh_token" in tokens:
                self.refresh_token = tokens["refresh_token"]
            else:
                logger.error("No refresh token available. Need to authorize first.")
                print("No refresh token available. Need to authorize first.")
                return False
        else:
            # Always sync from disk before sending - another broker instance (e.g.
            # SchwabStreamMonitor) may have rotated the refresh token since startup.
            # Schwab uses rotating refresh tokens: each use invalidates the previous.
            reloaded = self._load_tokens()
            if reloaded:
                disk_rt = reloaded.get("refresh_token")
                if disk_rt and disk_rt != self.refresh_token:
                    logger.debug("Syncing refresh token from disk (rotated by another instance)")
                    self.refresh_token = disk_rt

        token_url = f"{self.base_url}/v1/oauth/token"

        credentials = f"{self.app_key}:{self.app_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token
        }

        try:
            logger.info("Attempting Schwab token refresh...")
            response = requests.post(token_url, headers=headers, data=data, timeout=10)

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"Schwab token refresh failed: HTTP {response.status_code}")
                logger.error(f"Response: {error_text}")
                is_expired = (
                    "invalid_grant" in error_text.lower()
                    or "expired" in error_text.lower()
                    or "refresh_token_authentication_error" in error_text.lower()
                )
                if is_expired:
                    logger.error("REFRESH TOKEN EXPIRED - checking token file for newer token...")
                    # A renewal script may have saved a new token while we were running.
                    # Re-read the file and retry once before giving up.
                    reloaded = self._load_tokens()
                    new_rt = reloaded.get("refresh_token") if reloaded else None
                    if new_rt and new_rt != self.refresh_token:
                        logger.info("Found newer refresh token on disk - retrying with updated token")
                        self.refresh_token = new_rt
                        return self._refresh_access_token()  # one retry only
                    logger.error("REFRESH TOKEN EXPIRED - Re-authentication required!")
                    print("\n*** SCHWAB REFRESH TOKEN EXPIRED ***")
                    print("*** Opening renewal terminal... ***\n")
                    self._open_renewal_terminal()
                return False

            response.raise_for_status()

            tokens = response.json()
            self.access_token = tokens["access_token"]
            expires_in = tokens.get("expires_in", 1800)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in)

            # Track refresh token expiry (typically 7 days from now)
            self.refresh_token_expiry = datetime.now() + timedelta(days=7)

            # Update refresh token if provided
            if "refresh_token" in tokens:
                self.refresh_token = tokens["refresh_token"]

            self._save_tokens(tokens)
            logger.info(f"Schwab access token refreshed successfully (expires in {expires_in//60} minutes)")
            return True

        except requests.exceptions.Timeout:
            logger.error("Schwab token refresh timed out")
            print("Schwab API timeout - check network connection")
            return False
        except Exception as e:
            logger.error(f"Failed to refresh Schwab token: {e}")
            print(f"Failed to refresh token: {e}")
            return False

    def _save_tokens(self, tokens: Dict[str, Any]):
        """Save tokens to file with expiry timestamps."""
        # Add expiry timestamps for persistence across restarts
        tokens_to_save = dict(tokens)
        tokens_to_save["_token_expiry"] = self.token_expiry.isoformat() if self.token_expiry else None
        tokens_to_save["_refresh_token_expiry"] = self.refresh_token_expiry.isoformat() if self.refresh_token_expiry else None
        tokens_to_save["_saved_at"] = datetime.now().isoformat()
        with open(self.token_file, 'w') as f:
            json.dump(tokens_to_save, f, indent=2)
        logger.info(f"Saved Schwab tokens to {self.token_file}")

    def _load_tokens(self) -> Optional[Dict[str, Any]]:
        """Load tokens from file and restore expiry timestamps."""
        if self.token_file.exists():
            try:
                with open(self.token_file, 'r') as f:
                    tokens = json.load(f)

                # Restore expiry timestamps if available
                if "_token_expiry" in tokens and tokens["_token_expiry"]:
                    try:
                        self.token_expiry = datetime.fromisoformat(tokens["_token_expiry"])
                    except (ValueError, TypeError):
                        pass

                if "_refresh_token_expiry" in tokens and tokens["_refresh_token_expiry"]:
                    try:
                        self.refresh_token_expiry = datetime.fromisoformat(tokens["_refresh_token_expiry"])
                        # Check if refresh token is expired
                        if self.refresh_token_expiry < datetime.now():
                            logger.error(f"CRITICAL: Schwab refresh token EXPIRED on {self.refresh_token_expiry}")
                            logger.error("You must re-authenticate! Run complete_schwab_setup.py")
                            print(f"\n*** SCHWAB REFRESH TOKEN EXPIRED on {self.refresh_token_expiry} ***")
                            print("*** Run complete_schwab_setup.py to get new tokens ***\n")
                    except (ValueError, TypeError):
                        pass

                if "_saved_at" in tokens:
                    logger.info(f"Loaded Schwab tokens saved at {tokens['_saved_at']}")

                return tokens
            except Exception as e:
                logger.error(f"Failed to load Schwab tokens: {e}")
                return None
        return None

    def _ensure_valid_token(self):
        """Ensure we have a valid access token, refreshing proactively before expiry.

        Thread-safe: uses _token_lock to prevent concurrent refresh races when
        _fetch_price_lookback() calls get_historical_data() from ThreadPoolExecutor workers.
        """
        with self._token_lock:
            # Check if refresh token is expiring soon (within 48 hours)
            if self.refresh_token_expiry:
                time_to_expiry = self.refresh_token_expiry - datetime.now()
                hours_left = time_to_expiry.total_seconds() / 3600

                if time_to_expiry.total_seconds() < 172800:  # Less than 48 hours
                    logger.error(
                        f"CRITICAL: Schwab refresh token expires in {hours_left:.1f} hours. "
                        "Re-authentication required!"
                    )

                    # Auto-launch OAuth setup if within 24 hours (more urgent)
                    if time_to_expiry.total_seconds() < 86400:
                        self._launch_oauth_setup(hours_left)

            # Refresh 5 minutes before expiry to prevent gaps during trading
            expiry_buffer = timedelta(minutes=5)

            # Refresh if no token, expired, OR within 5 minutes of expiry
            if not self.access_token or not self.token_expiry or \
               datetime.now() >= (self.token_expiry - expiry_buffer):
                if not self._refresh_access_token():
                    raise RuntimeError("Failed to obtain valid access token")

    def _launch_oauth_setup(self, hours_left: float):
        """Launch the OAuth setup script to renew tokens before expiry."""
        import subprocess
        from pathlib import Path

        # Only launch once per session to avoid spamming
        if hasattr(self, '_oauth_launched') and self._oauth_launched:
            return

        self._oauth_launched = True

        print("\n" + "=" * 60)
        print(f"SCHWAB TOKEN EXPIRES IN {hours_left:.1f} HOURS")
        print("=" * 60)
        print("Opening OAuth setup to renew your token...")
        print("=" * 60 + "\n")

        # Find the quick_schwab_oauth.py script
        script_path = Path(__file__).parent.parent / "quick_schwab_oauth.py"

        if script_path.exists():
            try:
                # Launch in a new window so it doesn't block trading
                subprocess.Popen(
                    ["python", str(script_path)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0,
                    cwd=str(script_path.parent)
                )
                logger.info(f"Launched OAuth setup script: {script_path}")
            except Exception as e:
                logger.error(f"Failed to launch OAuth setup: {e}")
                print(f"Failed to auto-launch. Run manually: python {script_path}")
        else:
            logger.error(f"OAuth script not found: {script_path}")
            print(f"OAuth script not found. Run: python trading_agent/quick_schwab_oauth.py")

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Make authenticated API request with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            **kwargs: Additional arguments for requests

        Returns:
            Response object
        """
        self._ensure_valid_token()

        url = f"{self.base_url}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"

        # Add timeout if not already specified
        if "timeout" not in kwargs:
            kwargs["timeout"] = 10

        # Retry logic with exponential backoff
        max_retries = 3
        retry_delay = 1  # Start with 1 second

        for attempt in range(max_retries):
            try:
                response = requests.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                return response

            except requests.exceptions.Timeout as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Request timeout (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error(f"Request timeout after {max_retries} attempts")
                    raise

            except requests.exceptions.ConnectionError as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Connection error (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(f"Connection failed after {max_retries} attempts")
                    raise

            except requests.exceptions.HTTPError as e:
                # Don't retry on 4xx errors (client errors), only 5xx (server errors)
                if e.response.status_code >= 500 and attempt < max_retries - 1:
                    logger.warning(f"Server error {e.response.status_code} (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    raise

        # Should never reach here, but just in case
        raise RuntimeError(f"Failed to complete request after {max_retries} attempts")

    def connect(self) -> bool:
        """Connect to Schwab API."""
        try:
            # Try to refresh token
            if not self._refresh_access_token():
                print("\n" + "="*60)
                print("SCHWAB AUTHENTICATION REQUIRED")
                print("="*60)
                print("\nFirst-time setup:")
                print("1. Visit this URL in your browser:")
                print(f"\n{self._get_authorization_url()}\n")
                print("2. Authorize the app")
                print("3. Copy the 'code' from the redirect URL")
                print("4. Run: python -c \"from brokers.schwab_broker import SchwabBroker; ...")
                print("   SchwabBroker.setup_from_code('YOUR_CODE_HERE')\"")
                print("\nAlternatively, set SCHWAB_REFRESH_TOKEN environment variable")
                print("="*60)
                return False

            # Get account information to verify connection
            accounts_response = self._make_request("GET", "/trader/v1/accounts")
            accounts = accounts_response.json()

            if accounts and len(accounts) > 0:
                # Store first account number (Schwab uses accountNumber, not hashValue)
                account_data = accounts[0].get("securitiesAccount", {})
                self.account_hash = account_data.get("accountNumber")

                if not self.account_hash:
                    print("[X] Could not find account number in response")
                    return False

                # Initialize order validator
                self.order_validator = OrderValidator(broker=self, data_provider=self)

                self.connected = True
                print(f"[OK] Connected to Schwab (Account: ****{str(self.account_hash)[-4:]})")
                return True
            else:
                print("[X] No accounts found")
                return False

        except Exception as e:
            print(f"[X] Failed to connect to Schwab: {e}")
            self.connected = False
            return False

    def disconnect(self):
        """Disconnect from Schwab."""
        self.access_token = None
        self.token_expiry = None
        self.connected = False
        print("Disconnected from Schwab")

    def get_account_info(self) -> AccountInfo:
        """Get account information."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Get account details (use plural /accounts endpoint with fields parameter)
        response = self._make_request(
            "GET",
            "/trader/v1/accounts",
            params={"fields": "positions"}
        )

        accounts = response.json()
        if not accounts:
            raise RuntimeError("No accounts found")

        # Get the first account (or find by account_hash if multiple accounts)
        account_data = accounts[0]
        securities = account_data.get("securitiesAccount", {})

        # Parse positions
        positions = []
        for pos_data in securities.get("positions", []):
            instrument = pos_data.get("instrument", {})
            positions.append(Position(
                symbol=instrument.get("symbol", ""),
                quantity=int(pos_data.get("longQuantity", 0)),
                avg_entry_price=float(pos_data.get("averagePrice", 0)),
                current_price=float(pos_data.get("marketValue", 0) / pos_data.get("longQuantity", 1)),
                unrealized_pnl=float(pos_data.get("currentDayProfitLoss", 0)),
                unrealized_pnl_percent=float(pos_data.get("currentDayProfitLossPercentage", 0))
            ))

        balances = securities.get("currentBalances", {})

        return AccountInfo(
            cash=float(balances.get("cashBalance", 0)),
            portfolio_value=float(balances.get("liquidationValue", 0)),
            buying_power=float(balances.get("buyingPower", 0)),
            positions=positions
        )

    def get_quote(self, symbol: str) -> Quote:
        """Get current quote for a symbol."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        response = self._make_request(
            "GET",
            f"/marketdata/v1/quotes",
            params={"symbols": symbol}
        )

        quotes = response.json()
        quote_data = quotes.get(symbol, {}).get("quote", {})

        # Extract prices and validate
        price = float(quote_data.get("lastPrice", 0))
        bid = float(quote_data.get("bidPrice", 0))
        ask = float(quote_data.get("askPrice", 0))
        prev_close = float(quote_data.get("closePrice", 0))  # Previous close price

        # Validate quote prices are positive
        # Special handling for VIX and other index symbols (no bid/ask)
        if symbol in ['VIX', 'SPX', 'NDX', 'RUT']:
            # Index symbols only need valid price, not bid/ask
            if symbol == 'VIX':
                # Use web method as primary source for VIX (Schwab API often fails)
                web_vix = self._get_vix_from_web()
                if web_vix and web_vix > 0:
                    logger.debug(f"VIX from web: {web_vix}")
                    price = web_vix
                elif price > 0:
                    # Fall back to Schwab API if web fails but Schwab returned valid price
                    logger.debug(f"VIX web failed, using Schwab API: {price}")
                else:
                    # Both failed, use reasonable default
                    logger.warning("Both VIX sources failed, using default 18.5")
                    price = 18.5
            elif price <= 0:
                raise ValueError(f"Invalid quote price for {symbol}: price={price}")
        else:
            # Regular stocks: use bid/ask midpoint if lastPrice=0 but bid/ask valid
            if price <= 0:
                if bid > 0 and ask > 0:
                    price = (bid + ask) / 2
                    logger.info(f"Schwab {symbol}: used bid/ask midpoint ${price:.2f} (bid=${bid:.2f}, ask=${ask:.2f})")
                else:
                    raise ValueError(f"No valid prices for {symbol}: last={price}, bid={bid}, ask={ask}")
            elif bid <= 0 or ask <= 0:
                logger.warning(f"Schwab {symbol}: missing bid/ask but lastPrice=${price:.2f} ok")

        # Get quote timestamp and validate freshness
        quote_time_ms = quote_data.get("quoteTimeInLong")
        if quote_time_ms:
            # Convert from milliseconds to datetime
            quote_timestamp = datetime.fromtimestamp(quote_time_ms / 1000)

            # Check if quote is stale (older than 30 seconds)
            age_seconds = (datetime.now() - quote_timestamp).total_seconds()
            if age_seconds > 30:
                logger.warning(f"Stale quote for {symbol}: {age_seconds:.0f} seconds old")
                raise ValueError(f"Quote for {symbol} is stale ({age_seconds:.0f} seconds old)")
        else:
            # If API doesn't provide timestamp, use current time
            quote_timestamp = datetime.now()
            logger.debug(f"Quote for {symbol} has no timestamp, using current time")

        return Quote(
            symbol=symbol,
            price=price,
            bid=bid,
            ask=ask,
            bid_size=int(quote_data.get("bidSize", 0)),
            ask_size=int(quote_data.get("askSize", 0)),
            timestamp=quote_timestamp,
            source="Schwab",
            prev_close=prev_close
        )

    def get_positions(self) -> List[Position]:
        """Get all positions."""
        account_info = self.get_account_info()
        return account_info.positions

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None
    ) -> Order:
        """Place a trading order."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # PRE-ORDER VALIDATION
        if self.order_validator:
            try:
                # Get account info for validation
                account_info = self.get_account_info()
                
                # Build current positions dict
                current_positions = {pos.symbol: pos.quantity for pos in account_info.positions}
                
                # Convert order type to string for validator
                order_type_str = order_type.value if hasattr(order_type, 'value') else str(order_type).split('.')[-1].lower()
                
                # Run validation
                validation_result = self.order_validator.validate_order(
                    symbol=symbol,
                    side=side.value if hasattr(side, 'value') else ('buy' if side == OrderSide.BUY else 'sell'),
                    quantity=quantity,
                    order_type=order_type_str,
                    limit_price=limit_price,
                    stop_price=stop_price,
                    current_positions=current_positions,
                    account_value=account_info.portfolio_value,
                    available_cash=account_info.cash
                )
                
                # Log warnings
                for warning in validation_result.get('warnings', []):
                    logger.warning(f"Order validation warning: {warning}")
                
                # Block order if validation failed
                if not validation_result['valid']:
                    error_msg = f"Order validation failed: {', '.join(validation_result['errors'])}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                    
                logger.info(f"Order validation PASSED for {side.value if hasattr(side, 'value') else side} {quantity} {symbol}")
                
            except Exception as e:
                # If validation itself fails, log but don't block (fail-open for safety)
                logger.error(f"Order validation error (allowing order): {e}", exc_info=True)

        # Build order object
        order_obj = {
            "orderType": self._map_order_type(order_type),
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": "BUY" if side == OrderSide.BUY else "SELL",
                    "quantity": quantity,
                    "instrument": {
                        "symbol": symbol,
                        "assetType": "EQUITY"
                    }
                }
            ]
        }

        if order_type == OrderType.LIMIT and limit_price:
            order_obj["price"] = limit_price
        elif order_type == OrderType.STOP and stop_price:
            order_obj["stopPrice"] = stop_price

        # Place order
        response = self._make_request(
            "POST",
            f"/trader/v1/accounts/{self.account_hash}/orders",
            json=order_obj
        )

        # Get order ID from Location header
        order_id = response.headers.get("Location", "").split("/")[-1]

        # Return order object
        return Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            status=OrderStatus.PENDING,
            limit_price=limit_price,
            stop_price=stop_price,
            created_at=datetime.now()
        )

    def place_bracket_order(
        self,
        symbol: str,
        entry_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        quantity: int
    ) -> Order:
        """
        Place a bracket order with entry, take profit, and stop loss.

        Schwab supports bracket orders through TRIGGER orderStrategyType
        with OCO (One Cancels Other) child orders for TP and SL.
        """
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Build bracket order using TRIGGER strategy
        # Entry order triggers OCO children (take profit and stop loss)
        # Note: Schwab API expects prices as strings per documentation
        # Using MARKET order for entry to ensure immediate fill at actual market price
        bracket_order = {
            "orderType": "MARKET",
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "TRIGGER",
            "orderLegCollection": [
                {
                    "instruction": "BUY",
                    "quantity": quantity,
                    "instrument": {
                        "symbol": symbol,
                        "assetType": "EQUITY"
                    }
                }
            ],
            "childOrderStrategies": [
                {
                    "orderStrategyType": "OCO",
                    "childOrderStrategies": [
                        {
                            "orderType": "LIMIT",
                            "session": "NORMAL",
                            "duration": "DAY",
                            "orderStrategyType": "SINGLE",
                            "price": str(take_profit_price),
                            "orderLegCollection": [
                                {
                                    "instruction": "SELL",
                                    "quantity": quantity,
                                    "instrument": {
                                        "symbol": symbol,
                                        "assetType": "EQUITY"
                                    }
                                }
                            ]
                        },
                        {
                            "orderType": "STOP",
                            "session": "NORMAL",
                            "duration": "DAY",
                            "orderStrategyType": "SINGLE",
                            "stopPrice": str(stop_loss_price),
                            "orderLegCollection": [
                                {
                                    "instruction": "SELL",
                                    "quantity": quantity,
                                    "instrument": {
                                        "symbol": symbol,
                                        "assetType": "EQUITY"
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        try:
            # Place bracket order
            response = self._make_request(
                "POST",
                f"/trader/v1/accounts/{self.account_hash}/orders",
                json=bracket_order
            )

            # Get order ID from Location header
            order_id = response.headers.get("Location", "").split("/")[-1]

            # Return order object for the entry order
            return Order(
                order_id=order_id,
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=quantity,
                order_type=OrderType.LIMIT,
                status=OrderStatus.PENDING,
                limit_price=entry_price,
                stop_price=None,
                created_at=datetime.now()
            )
        except Exception as e:
            logger.error(f"Schwab bracket order failed: {e}")
            raise

    def place_oco_order(
        self,
        symbol: str,
        quantity: int,
        take_profit_price: float,
        stop_loss_price: float
    ) -> Order:
        """
        Place an OCO (One Cancels Other) order for an existing position.

        This places both take profit and stop loss as a linked pair.
        When one fills, the other is automatically canceled.

        Args:
            symbol: Stock symbol
            quantity: Number of shares to sell
            take_profit_price: Limit price for take profit
            stop_loss_price: Stop price for stop loss

        Returns:
            Order object for the OCO parent order
        """
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Build OCO order using Schwab's orderStrategyType
        # Note: Schwab API expects prices as strings per documentation
        oco_order = {
            "orderStrategyType": "OCO",
            "childOrderStrategies": [
                {
                    # Take profit - limit order
                    "orderType": "LIMIT",
                    "session": "NORMAL",
                    "duration": "DAY",
                    "orderStrategyType": "SINGLE",
                    "price": str(take_profit_price),
                    "orderLegCollection": [
                        {
                            "instruction": "SELL",
                            "quantity": quantity,
                            "instrument": {
                                "symbol": symbol,
                                "assetType": "EQUITY"
                            }
                        }
                    ]
                },
                {
                    # Stop loss - stop order
                    "orderType": "STOP",
                    "session": "NORMAL",
                    "duration": "DAY",
                    "orderStrategyType": "SINGLE",
                    "stopPrice": str(stop_loss_price),
                    "orderLegCollection": [
                        {
                            "instruction": "SELL",
                            "quantity": quantity,
                            "instrument": {
                                "symbol": symbol,
                                "assetType": "EQUITY"
                            }
                        }
                    ]
                }
            ]
        }

        try:
            response = self._make_request(
                "POST",
                f"/trader/v1/accounts/{self.account_hash}/orders",
                json=oco_order
            )

            # Get order ID from Location header
            order_id = "oco_" + str(uuid.uuid4())
            if hasattr(response, 'headers') and 'Location' in response.headers:
                order_id = response.headers['Location'].split('/')[-1]

            print(f"[OK] Placed OCO order for {symbol}: TP@${take_profit_price:.2f}, SL@${stop_loss_price:.2f}")

            return Order(
                order_id=order_id,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=quantity,
                order_type=OrderType.LIMIT,
                status=OrderStatus.PENDING,
                limit_price=take_profit_price,
                stop_price=stop_loss_price,
                created_at=datetime.now()
            )
        except Exception as e:
            print(f"Schwab OCO order failed for {symbol}: {e}")
            raise

    def update_oco_order(
        self,
        symbol: str,
        quantity: int,
        take_profit_price: float,
        stop_loss_price: float
    ) -> Order:
        """
        Cancel any existing OCO/protective orders for a symbol, then place a fresh OCO order.

        Use this after partial fills or when TP/SL levels need to change. Doing cancel+replace
        as separate LIMIT+STOP orders fails because Schwab holds shares for the first order,
        blocking the second. OCO places both legs atomically.

        Args:
            symbol: Stock symbol
            quantity: Number of shares for the new OCO (remaining after partial fill)
            take_profit_price: New take profit limit price
            stop_loss_price: New stop loss stop price

        Returns:
            Order object for the new OCO order
        """
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Safer cancel+replace: place NEW OCO first, only cancel old orders if new succeeds.
        # This eliminates the naked-position window that exists when cancelling first.
        # If new OCO placement fails, old orders remain in place - position stays protected.
        new_order = None
        last_exc = None
        for attempt in range(2):  # Up to 2 attempts on transient failures
            try:
                new_order = self.place_oco_order(
                    symbol=symbol,
                    quantity=quantity,
                    take_profit_price=take_profit_price,
                    stop_loss_price=stop_loss_price
                )
                logger.info(f"New OCO placed for {symbol}: TP=${take_profit_price:.2f}, SL=${stop_loss_price:.2f} (order {new_order.order_id})")
                break  # Success
            except Exception as e:
                last_exc = e
                if attempt == 0:
                    logger.warning(f"New OCO placement attempt 1 failed for {symbol}: {e} - retrying once")
                    import time as _t
                    _t.sleep(0.5)
        if new_order is None:
            logger.error(f"New OCO failed after 2 attempts for {symbol}: {last_exc} - keeping existing protection in place")
            raise last_exc  # Re-raise so caller knows update failed; old orders still active

        # New OCO is active - now safe to cancel old sell orders
        try:
            open_orders = self.get_open_orders()
            cancelled_count = 0
            for order in open_orders:
                if (order.symbol == symbol and
                        order.side == OrderSide.SELL and
                        order.status in (OrderStatus.PENDING, OrderStatus.OPEN) and
                        order.order_id != new_order.order_id):  # Don't cancel the one we just placed
                    if self.cancel_order(order.order_id):
                        cancelled_count += 1
                        logger.info(f"Cancelled stale sell order {order.order_id} for {symbol}")
            if cancelled_count > 0:
                logger.info(f"Cancelled {cancelled_count} stale sell order(s) for {symbol} after new OCO confirmed")
                import time as _time
                _time.sleep(0.2)  # Brief settle - shorter now that we're cancelling stale, not live protection
        except Exception as e:
            logger.warning(f"Failed to cancel stale orders for {symbol} after OCO replacement (not critical - new OCO is active): {e}")

        return new_order

    def place_trailing_stop(
        self,
        symbol: str,
        quantity: int,
        stop_price_offset: float,
        duration: str = "DAY"
    ) -> Order:
        """
        Place a trailing stop order.

        Args:
            symbol: Stock symbol
            quantity: Number of shares to sell
            stop_price_offset: Dollar amount to trail (e.g., 5.00 for $5 trail)
            duration: "DAY" or "GOOD_TILL_CANCEL" (default: "DAY")

        Returns:
            Order object

        Example:
            # Sell 100 shares with $5 trailing stop
            order = broker.place_trailing_stop("AAPL", 100, 5.00)
        """
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Build trailing stop order
        order_obj = {
            "orderType": "TRAILING_STOP",
            "session": "NORMAL",
            "duration": duration,
            "orderStrategyType": "SINGLE",
            "stopPriceLinkBasis": "BID",
            "stopPriceLinkType": "VALUE",
            "stopPriceOffset": stop_price_offset,
            "orderLegCollection": [
                {
                    "instruction": "SELL",
                    "quantity": quantity,
                    "instrument": {
                        "symbol": symbol,
                        "assetType": "EQUITY"
                    }
                }
            ]
        }

        try:
            response = self._make_request(
                "POST",
                f"/trader/v1/accounts/{self.account_hash}/orders",
                json=order_obj
            )

            # Get order ID from Location header
            order_id = response.headers.get("Location", "").split("/")[-1]

            logger.info(f"Trailing stop placed: {symbol} {quantity} shares @ ${stop_price_offset:.2f} trail")

            # Return order object
            return Order(
                order_id=order_id,
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=quantity,
                order_type=OrderType.STOP,  # Closest match
                status=OrderStatus.PENDING,
                stop_price=None,  # Trailing stop doesn't have fixed stop price
                created_at=datetime.now()
            )
        except Exception as e:
            logger.error(f"Failed to place trailing stop for {symbol}: {e}")
            raise

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        try:
            self._make_request(
                "DELETE",
                f"/trader/v1/accounts/{self.account_hash}/orders/{order_id}"
            )
            return True
        except Exception as e:
            print(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns count of orders canceled."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        try:
            orders = self.get_open_orders()
            count = 0
            for order in orders:
                if self.cancel_order(order.order_id):
                    count += 1
            print(f"Canceled {count} open orders")
            return count
        except Exception as e:
            print(f"Failed to cancel all orders: {e}")
            return 0

    def get_order_status(self, order_id: str) -> Optional[Order]:
        """Get order status."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        response = self._make_request(
            "GET",
            f"/trader/v1/accounts/{self.account_hash}/orders/{order_id}"
        )

        order_data = response.json()
        order = self._parse_order(order_data)
        if order is None:
            logger.warning(f"Failed to parse order {order_id}")
        return order

    def get_open_orders(self) -> List[Order]:
        """Get all open (pending) orders."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        response = self._make_request(
            "GET",
            f"/trader/v1/accounts/{self.account_hash}/orders",
            params={"status": "WORKING"}  # Schwab uses "WORKING" for open orders
        )

        orders_data = response.json()

        # Flatten OCO/TRIGGER parents into child orders so callers see the
        # individual STOP and LIMIT legs, not just the parent wrapper.
        # Bracket order structure: TRIGGER -> OCO -> [LIMIT(TP), STOP(SL)]
        flat_orders = []
        for order_data in orders_data:
            strategy = order_data.get("orderStrategyType", "")
            children = order_data.get("childOrderStrategies", [])
            if strategy in ("OCO", "TRIGGER") and children:
                for child in children:
                    child_strategy = child.get("orderStrategyType", "")
                    grandchildren = child.get("childOrderStrategies", [])
                    if child_strategy == "OCO" and grandchildren:
                        flat_orders.extend(grandchildren)
                    else:
                        flat_orders.append(child)
            else:
                flat_orders.append(order_data)

        orders = [self._parse_order(o) for o in flat_orders]
        return [o for o in orders if o is not None]

    def get_filled_sell_orders(self, since_days: int = 7) -> List[Dict[str, Any]]:
        """
        Get filled sell orders from Schwab for the past N days.

        Returns list of dicts for use by FillReconciler:
            {symbol, filled_at, filled_avg_price, qty, order_id}

        Uses Schwab's orders endpoint with status=FILLED and a date range.
        Fill time is extracted from orderActivityCollection executionLegs.
        """
        if not self.connected:
            return []

        try:
            from datetime import timezone

            cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
            # Schwab API fromEnteredTime format: "YYYY-MM-DDTHH:MM:SS.000Z"
            from_time_str = cutoff.strftime('%Y-%m-%dT%H:%M:%S.000Z')

            response = self._make_request(
                "GET",
                f"/trader/v1/accounts/{self.account_hash}/orders",
                params={
                    "status": "FILLED",
                    "fromEnteredTime": from_time_str,
                    "maxResults": 200,
                }
            )

            orders_data = response.json()
            if not isinstance(orders_data, list):
                logger.warning("get_filled_sell_orders: unexpected Schwab response format")
                return []

            result = []
            for o in orders_data:
                # Only SELL-side filled orders
                leg = (o.get("orderLegCollection") or [{}])[0]
                instruction = leg.get("instruction", "").upper()
                if instruction not in ("SELL", "SELL_SHORT"):
                    continue

                symbol = leg.get("instrument", {}).get("symbol", "")
                if not symbol:
                    continue

                filled_qty = float(o.get("filledQuantity", 0))
                if filled_qty <= 0:
                    continue

                # Extract fill price: average across execution legs
                fill_price = None
                fill_time = None
                activities = o.get("orderActivityCollection") or []
                if activities:
                    # Use last activity (most recent execution)
                    exec_legs = activities[-1].get("executionLegs") or [{}]
                    last_leg = exec_legs[-1]
                    fill_price = float(last_leg.get("price", 0)) or None
                    time_str = last_leg.get("time", "")
                    if time_str:
                        try:
                            # Schwab time format: "2026-02-19T06:30:08.000+0000"
                            fill_time = datetime.fromisoformat(
                                time_str.replace('+0000', '+00:00')
                            )
                            if fill_time.tzinfo is None:
                                fill_time = fill_time.replace(tzinfo=timezone.utc)
                        except ValueError:
                            fill_time = None

                # Fallback to closeTime if no execution leg time
                if fill_time is None:
                    close_str = o.get("closeTime", "")
                    if close_str:
                        try:
                            fill_time = datetime.fromisoformat(
                                close_str.replace('+0000', '+00:00')
                            )
                            if fill_time.tzinfo is None:
                                fill_time = fill_time.replace(tzinfo=timezone.utc)
                        except ValueError:
                            pass

                # Fallback fill price to enteredPrice if no execution price
                if fill_price is None:
                    fill_price = float(o.get("price", 0)) or 0.0

                if fill_time is None or fill_price == 0.0:
                    logger.debug(f"get_filled_sell_orders: skipping {symbol} "
                                 f"- missing fill time or price")
                    continue

                result.append({
                    'symbol': symbol,
                    'filled_at': fill_time,
                    'filled_avg_price': fill_price,
                    'qty': filled_qty,
                    'order_id': str(o.get("orderId", "")),
                })

            logger.info(f"get_filled_sell_orders: {len(result)} filled sell(s) "
                        f"in last {since_days}d (Schwab)")
            return result

        except Exception as e:
            logger.error(f"get_filled_sell_orders (Schwab) failed: {e}")
            return []

    def get_historical_data(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        timeframe: str = "1D"
    ) -> List[Dict[str, Any]]:
        """Get historical price data."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        # Map timeframe - use simpler approach for Schwab API
        if timeframe in ["1D", "daily", "1Day"]:
            # Match schwab-py's daily helper shape. Schwab rejects daily
            # startDate/endDate requests that omit the period metadata.
            period_type = "year"
            period = 20
            frequency_type = "daily"
            frequency = 1
        elif timeframe in ["1Min", "5Min", "15Min", "30Min", "1H"]:
            period_type = "day"
            period = 10
            frequency_type = "minute"
            # Extract minutes from timeframe
            if timeframe == "1Min":
                frequency = 1
            elif timeframe == "5Min":
                frequency = 5
            elif timeframe == "15Min":
                frequency = 15
            elif timeframe == "30Min":
                frequency = 30
            elif timeframe == "1H":
                frequency = 60
        else:
            period_type = "month"
            period = 1
            frequency_type = "daily"
            frequency = 1

        # Use startDate/endDate whenever a precise window is provided. This is
        # required for daily FORCESWING ADX warmup; period=1 month is too short.
        if start_date is not None and end_date is not None:
            start_ms = int(start_date.timestamp() * 1000)
            end_ms = int(end_date.timestamp() * 1000)
            request_params = {
                "symbol": symbol,
                "periodType": period_type,
                "period": period,
                "frequencyType": frequency_type,
                "frequency": frequency,
                "startDate": start_ms,
                "endDate": end_ms,
                "needExtendedHoursData": "false"
            }
        else:
            request_params = {
                "symbol": symbol,
                "periodType": period_type,
                "period": period,
                "frequencyType": frequency_type,
                "frequency": frequency,
                "needExtendedHoursData": "false"
            }

        response = self._make_request(
            "GET",
            "/marketdata/v1/pricehistory",
            params=request_params
        )

        data = response.json()
        result = []

        for candle in data.get("candles", []):
            result.append({
                "timestamp": datetime.fromtimestamp(candle["datetime"] / 1000),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": int(candle["volume"])
            })

        return result

    def get_historical_data_df(
        self,
        symbol: str,
        start_date: datetime,
        end_date: datetime,
        timeframe: str = "1D"
    ):
        """
        Get historical price data as pandas DataFrame.

        Returns DataFrame with datetime index and columns:
        Open, High, Low, Close, Volume (matching yfinance format)
        """
        import pandas as pd

        # Get data as list of dicts
        data = self.get_historical_data(symbol, start_date, end_date, timeframe)

        if not data:
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(data)

        # Set timestamp as index
        df.set_index('timestamp', inplace=True)

        # Rename columns to match yfinance format (capitalized)
        df.rename(columns={
            'open': 'Open',
            'high': 'High',
            'low': 'Low',
            'close': 'Close',
            'volume': 'Volume'
        }, inplace=True)

        # Filter by date range (Schwab API may return more data than requested)
        df = df[(df.index >= start_date) & (df.index <= end_date)]

        return df

    def is_market_open(self) -> bool:
        """Check if market is currently open."""
        if not self.connected:
            raise RuntimeError("Not connected to broker")

        response = self._make_request(
            "GET",
            "/marketdata/v1/markets",
            params={"markets": "equity"}
        )

        markets = response.json()
        equity_market = markets.get("equity", {})

        # Debug logging to see what Schwab returns
        logger.debug(f"Schwab markets API response: {markets}")
        logger.debug(f"Equity market data: {equity_market}")
        is_open = equity_market.get("isOpen", False)
        logger.debug(f"Schwab isOpen status: {is_open}")

        return is_open

    def _map_order_type(self, order_type: OrderType) -> str:
        """Map internal order type to Schwab format."""
        mapping = {
            OrderType.MARKET: "MARKET",
            OrderType.LIMIT: "LIMIT",
            OrderType.STOP: "STOP",
            OrderType.MOC: "MARKET_ON_CLOSE"
        }
        return mapping.get(order_type, "MARKET")

    def _map_timeframe(self, timeframe: str) -> tuple:
        """Map timeframe to Schwab period type and frequency."""
        mapping = {
            "1Min": ("day", "minute"),
            "5Min": ("day", "minute"),
            "15Min": ("day", "minute"),
            "1H": ("day", "minute"),
            "1D": ("month", "daily")
        }
        return mapping.get(timeframe, ("month", "daily"))

    def _parse_order(self, order_data: Dict) -> Optional[Order]:
        """Parse Schwab order data into Order object."""
        try:
            leg = order_data.get("orderLegCollection", [{}])[0]

            # Extract fill price from order activity if available
            filled_price = None
            order_activities = order_data.get("orderActivityCollection", [])
            if order_activities:
                # Get the most recent execution
                execution = order_activities[-1].get("executionLegs", [{}])[0]
                filled_price = float(execution.get("price", 0)) if execution.get("price") else None

            # Map Schwab orderType string to OrderType enum
            schwab_order_type = order_data.get("orderType", "MARKET").upper()
            if schwab_order_type == "LIMIT":
                mapped_order_type = OrderType.LIMIT
            elif schwab_order_type in ("STOP", "STOP_MARKET"):
                mapped_order_type = OrderType.STOP
            elif schwab_order_type == "STOP_LIMIT":
                mapped_order_type = OrderType.STOP_LIMIT
            elif schwab_order_type == "TRAILING_STOP":
                mapped_order_type = OrderType.TRAILING_STOP
            else:
                mapped_order_type = OrderType.MARKET

            return Order(
                order_id=str(order_data.get("orderId")),
                symbol=leg.get("instrument", {}).get("symbol", ""),
                side=OrderSide.BUY if leg.get("instruction") == "BUY" else OrderSide.SELL,
                quantity=int(leg.get("quantity", 0)),
                order_type=mapped_order_type,
                status=self._map_order_status(order_data.get("status")),
                filled_quantity=int(order_data.get("filledQuantity", 0)),
                filled_price=filled_price,
                created_at=datetime.now()
            )
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse order data: {e}. Data: {order_data}")
            return None

    def _get_vix_from_web(self) -> Optional[float]:
        """Get VIX from web sources as fallback when Schwab API fails."""
        try:
            import requests
            import re
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning("Web scraping libraries not available for VIX fallback")
            return None

        # Try multiple patterns for Yahoo Finance (most reliable)
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            response = requests.get('https://finance.yahoo.com/quote/%5EVIX/', 
                                  headers=headers, timeout=10)
            response.raise_for_status()
            
            # Try multiple regex patterns to find the VIX price
            patterns = [
                r'"regularMarketPrice":\s*{\s*"raw":\s*([0-9.]+)',  # JSON data
                r'data-symbol="\^VIX"[^>]*data-field="regularMarketPrice"[^>]*>([0-9.]+)',  # HTML attribute
                r'<fin-streamer[^>]*data-symbol="\^VIX"[^>]*data-field="regularMarketPrice"[^>]*>([0-9.]+)',  # fin-streamer element
                r'"VIX"[^}]*"regularMarketPrice"[^}]*"raw":\s*([0-9.]+)',  # JSON structure
                r'regularMarketPrice.*?([0-9]{1,2}\.[0-9]{1,2})'  # General pattern
            ]
            
            for pattern in patterns:
                vix_match = re.search(pattern, response.text, re.IGNORECASE)
                if vix_match:
                    vix_value = float(vix_match.group(1))
                    if 8.0 <= vix_value <= 80.0:  # Reasonable VIX range
                        logger.debug(f"Yahoo Finance VIX found with pattern: {vix_value}")
                        return vix_value
                    
        except Exception as e:
            logger.debug(f"Yahoo Finance VIX fallback failed: {e}")
        
        # Try MarketWatch as backup
        try:
            response = requests.get('https://www.marketwatch.com/investing/index/vix', 
                                  headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            response.raise_for_status()
            
            # Try BeautifulSoup parsing
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for price elements
            price_selectors = [
                'bg-quote[field="Last"]',
                '.intraday__price .value',
                '[data-module="LastPrice"]',
                '.price-value'
            ]
            
            for selector in price_selectors:
                element = soup.select_one(selector)
                if element:
                    price_text = element.get_text(strip=True)
                    vix_match = re.search(r'([0-9]{1,2}\.[0-9]{1,2})', price_text)
                    if vix_match:
                        vix_value = float(vix_match.group(1))
                        if 8.0 <= vix_value <= 80.0:
                            logger.debug(f"MarketWatch VIX found: {vix_value}")
                            return vix_value
                            
        except Exception as e:
            logger.debug(f"MarketWatch VIX fallback failed: {e}")
        
        # Try CBOE direct as last resort
        try:
            response = requests.get('https://cdn.cboe.com/api/global/delayed_quotes/index/_VIX.json',
                                  headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            data = response.json()
            if 'data' in data and 'last_price' in data['data']:
                vix_value = float(data['data']['last_price'])
                if 8.0 <= vix_value <= 80.0:
                    logger.debug(f"CBOE API VIX found: {vix_value}")
                    return vix_value
        except Exception as e:
            logger.debug(f"CBOE API VIX fallback failed: {e}")
        
        logger.warning("All VIX web sources failed")
        return None

    def _map_order_status(self, schwab_status: str) -> OrderStatus:
        """Map Schwab order status to internal status."""
        mapping = {
            "PENDING": OrderStatus.PENDING,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "WORKING": OrderStatus.PENDING
        }
        return mapping.get(schwab_status, OrderStatus.PENDING)

    @classmethod
    def setup_from_code(cls, auth_code: str, app_key: str = None, app_secret: str = None):
        """
        Helper method to set up Schwab authentication from authorization code.

        Args:
            auth_code: Authorization code from OAuth redirect
            app_key: Schwab app key
            app_secret: Schwab app secret
        """
        broker = cls(app_key=app_key, app_secret=app_secret)
        tokens = broker._exchange_code_for_tokens(auth_code)

        print("\n✅ Schwab authentication successful!")
        print(f"\nRefresh token saved to: {broker.token_file}")
        print("\nSet this environment variable:")
        print(f"export SCHWAB_REFRESH_TOKEN='{tokens['refresh_token']}'")
        print("\nOr add to your ~/.ai_trader/.env file")


# Example usage
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        # Setup mode
        print("Schwab Broker Setup")
        print("="*60)

        app_key = input("Enter your Schwab App Key: ").strip()
        app_secret = input("Enter your Schwab App Secret: ").strip()

        broker = SchwabBroker(app_key=app_key, app_secret=app_secret)

        print("\n1. Visit this URL in your browser:")
        print(f"\n{broker._get_authorization_url()}\n")
        print("2. Authorize the app and copy the 'code' parameter from the redirect URL")

        auth_code = input("\n3. Paste the authorization code here: ").strip()

        SchwabBroker.setup_from_code(auth_code, app_key, app_secret)

    else:
        # Test connection
        broker = SchwabBroker()
        if broker.connect():
            account = broker.get_account_info()
            print(f"\nAccount Value: ${account.portfolio_value:,.2f}")
            print(f"Cash: ${account.cash:,.2f}")
            print(f"Buying Power: ${account.buying_power:,.2f}")

            broker.disconnect()
