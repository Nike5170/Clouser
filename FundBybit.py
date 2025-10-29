import asyncio
import aiohttp
import json
import time
import hmac
import hashlib
from urllib.parse import urlencode
from datetime import datetime

API_KEY    = "TKCSBRh0oWIXaS3SC0"
API_SECRET = "MgAq2McTsjs4w1iwpGKyRRynWe5BGNUo61m9"
SYMBOL     = "ENSOUSDT"         # пример пары
QTY        = 4                  # пример объём
BASE       = "https://api.bybit.com"
WS_PRIVATE = "wss://stream.bybit.com/v5/private"  # приватный поток
WS_PUBLIC  = "wss://stream.bybit.com/v5/public/linear"  # публичный поток (исправлено)

ENTRY_THRESHOLD = -0.01
CHECK_WINDOW_MS = 30000
ENTRY_OFFSET_MS = 200

def log(msg):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}")

def sign(secret: str, params: dict) -> str:
    q = urlencode(params)
    return hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()

async def signed_req(session, method, path, params=None):
    params = params or {}
    params["apiKey"] = API_KEY
    params["timestamp"] = int(time.time() * 1000)
    params["sign"] = sign(API_SECRET, params)
    url = BASE + path
    async with session.request(method, url, params=params) as r:
        text = await r.text()
        if r.status != 200:
            log(f"[ERROR] {r.status}: {text}")
            raise Exception(f"HTTP {r.status}: {text}")
        return json.loads(text)

async def market_order(session, side, qty):
    body = {
        "category": "linear",
        "symbol": SYMBOL,
        "side": side.upper(),
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "ImmediateOrCancel",
        "reduceOnly": False
    }
    res = await signed_req(session, "POST", "/v5/order/create", body)
    log(f"[ORDER] {side} {qty}: {res}")
    return res

async def get_position(session):
    body = {"category": "linear", "symbol": SYMBOL}
    res = await signed_req(session, "GET", "/v5/position/list", body)
    for p in res.get("result", {}).get("list", []):
        if p.get("symbol") == SYMBOL:
            return float(p.get("size", 0)) * (1 if p.get("side") == "Buy" else -1)
    return 0.0

async def account_ws(session, funding_event):
    async with session.ws_connect(WS_PRIVATE) as ws:
        log("[WS] Connected account private stream")
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            log(f"[ACCOUNT_WS] {data}")
            if data.get("topic", "").startswith("funding") or data.get("type") == "funding":
                log("[EVENT] FUNDING received")
                if not funding_event.is_set():
                    funding_event.set()

async def run():
    funding_event = asyncio.Event()
    async with aiohttp.ClientSession() as session:
        account_task = asyncio.create_task(account_ws(session, funding_event))
        entered = False

        # Подключаемся к публичному WS
        async with session.ws_connect(WS_PUBLIC) as ws_mark:
            log(f"[WS] Connected public stream for {SYMBOL}")
            # Подписка на funding rate канала
            await ws_mark.send_json({
                "op": "subscribe",
                "args": [f"funding_rate.{SYMBOL}"]
            })

            async for msg in ws_mark:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                # Проверяем структуру данных
                # Bybit v5 funding_rate: {"topic": "funding_rate.ENSOUSDT", "data": [{"fundingRate": "...", "nextFundingTime": "..."}]}
                if "data" not in d:
                    continue

                for entry in d["data"]:
                    rate = float(entry.get("fundingRate", 0))
                    next_T = int(entry.get("nextFundingTime", 0))
                    now = int(time.time() * 1000)
                    ms_to_funding = next_T - now

                    log(f"[MARKET_STREAM] rate={rate:.6f}, nextFunding={next_T}, ms_to_funding={ms_to_funding}")

                    if (not entered) and (rate <= ENTRY_THRESHOLD) and (0 < ms_to_funding <= CHECK_WINDOW_MS):
                        entry_time = next_T - ENTRY_OFFSET_MS
                        delay = max(0, (entry_time - now) / 1000.0)
                        log(f"[READY] rate={rate:.6f}, entry in {delay*1000:.0f} ms")
                        await asyncio.sleep(delay)
                        log(f"[ENTRY] MARKET BUY {QTY}")
                        await market_order(session, "Buy", QTY)
                        entered = True

                    if entered and funding_event.is_set():
                        funding_event.clear()
                        pos = await get_position(session)
                        log(f"[POSITION] size={pos}")
                        if pos != 0:
                            side = "Sell" if pos > 0 else "Buy"
                            log(f"[EXIT] Closing position {pos} via {side}")
                            await market_order(session, side, abs(pos))
                        else:
                            log("[EXIT] No position to close")
                        entered = False

                await asyncio.sleep(0.1)

        await account_task

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log("Stopped manually.")
