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

#ENTRY_THRESHOLD = -0.01
CHECK_WINDOW_MS = 30000
ENTRY_OFFSET_MS = 50

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

async def schedule_entry(session, next_T):
    """Асинхронная задача для точного открытия позиции один раз"""
    now = int(time.time() * 1000)
    delay = max(0, (next_T - ENTRY_OFFSET_MS - now) / 1000.0)
    log(f"[TIMER] Waiting {delay*1000:.0f} ms until entry")
    await asyncio.sleep(delay)
    log(f"[ENTRY] MARKET BUY {QTY}")
    await market_order(session, "Buy", QTY)

async def run():
    funding_event = asyncio.Event()
    async with aiohttp.ClientSession() as session:
        account_task = asyncio.create_task(account_ws(session, funding_event))
        entry_task = None
        entered = False
        timer_started = False  # флаг, чтобы таймер создавался только один раз

        async with session.ws_connect(WS_PUBLIC) as ws_mark:
            log(f"[WS] Connected public stream for {SYMBOL}")
            await ws_mark.send_json({
                "op": "subscribe",
                "args": [f"funding_rate.{SYMBOL}"]
            })

            async for msg in ws_mark:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                if "data" not in d:
                    continue

                for entry in d["data"]:
                    next_T = int(entry.get("nextFundingTime", 0))
                    now = int(time.time() * 1000)
                    ms_to_funding = next_T - now

                    log(f"[MARKET_STREAM] nextFunding={next_T}, ms_to_funding={ms_to_funding}")

                    # Создаём таймер на открытие позиции только один раз
                    if not timer_started:
                        entry_task = asyncio.create_task(schedule_entry(session, next_T))
                        timer_started = True
                        entered = True

                    # Закрытие позиции сразу, когда ms_to_funding < 0
                    if entered and ms_to_funding < 0:
                        pos = await get_position(session)
                        log(f"[POSITION] size={pos}")
                        if pos != 0:
                            side = "Sell" if pos > 0 else "Buy"
                            log(f"[EXIT] Closing position {pos} via {side}")
                            await market_order(session, side, abs(pos))
                        else:
                            log("[EXIT] No position to close")
                        entered = False
                        if entry_task:
                            entry_task.cancel()
                            entry_task = None

                await asyncio.sleep(0.01)

        await account_task

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log("Stopped manually.")
