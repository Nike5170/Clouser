import asyncio, aiohttp, json, time, hmac, hashlib
from urllib.parse import urlencode
from datetime import datetime

API_KEY    = "9TvpACxlJtkRD6s22omjR7DzoZaBMouRUgtNuZAsemjwr50SE0rHOfn1u742BAqV"
API_SECRET = "tv5mhBQCuQYWE8qfrmk7O7a7Wtq9bckZvgNgE29SEhGRb0L1998g3ktjpwJxZwi6"
SYMBOL     = "ENSOUSDT"
QTY        = 10
BASE       = "https://fapi.binance.com"
WS_MARK    = f"wss://fstream.binance.com/ws/{SYMBOL.lower()}@markPrice@1s"
ENTRY_OFFSET_MS = 200
CHECK_WINDOW_MS = 30000

def log(msg):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}")

def sign(params):
    q = urlencode(params)
    return hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()

async def signed_req(session, method, path, params=None):
    params = params or {}
    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}
    async with session.request(method, BASE + path, params=params, headers=headers) as r:
        t = await r.text()
        if r.status != 200:
            log(f"[ERROR] {r.status}: {t}")
        return json.loads(t)

async def market_order(session, side, qty, reduce=False):
    p = {"symbol": SYMBOL, "side": side, "type": "MARKET", "quantity": str(qty)}
    if reduce:
        p["reduceOnly"] = "true"
    res = await signed_req(session, "POST", "/fapi/v1/order", p)
    log(f"[ORDER] {side} {qty}: {res}")

async def current_pos(session):
    d = await signed_req(session, "GET", "/fapi/v2/positionRisk", {"symbol": SYMBOL})
    try:
        return float(d[0]["positionAmt"])
    except:
        return 0.0

async def funding_info(session):
    d = await signed_req(session, "GET", "/fapi/v1/premiumIndex", {"symbol": SYMBOL})
    funding_rate = float(d.get("lastFundingRate", 0))
    next_funding = int(d.get("nextFundingTime", 0))
    return funding_rate, next_funding

async def get_listen_key(session):
    headers = {"X-MBX-APIKEY": API_KEY}
    async with session.post(BASE + "/fapi/v1/listenKey", headers=headers) as r:
        data = await r.json()
        return data["listenKey"]

async def account_ws(listen_key, funding_event):
    url = f"wss://fstream.binance.com/ws/{listen_key}"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            log("[WS] Connected user data stream")
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("e") == "ACCOUNT_UPDATE" and data["a"].get("m") == "FUNDING_FEE":
                    log(f"[EVENT] FUNDING_FEE received: {data['a']['B']}")
                    funding_event.set()  # сигнал для основного потока

async def run():
    funding_event = asyncio.Event()
    async with aiohttp.ClientSession() as session:
        # Показываем информацию о следующем funding сразу
        rate, next_funding = await funding_info(session)
        next_dt = datetime.utcfromtimestamp(next_funding / 1000).strftime("%Y-%m-%d %H:%M:%S")
        log(f"[INFO] Next funding time: {next_dt}, funding rate: {rate:.6f}")

        listen_key = await get_listen_key(session)
        asyncio.create_task(account_ws(listen_key, funding_event))

        async with session.ws_connect(WS_MARK) as ws:
            log(f"[WS] Connected markPrice for {SYMBOL}")
            entered = False
            entry_T = next_funding  # будем заходить прямо перед funding

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)
                next_T = int(d["T"])
                now = int(time.time() * 1000)
                ms_to_funding = entry_T - now

                # логируем markPrice в ±2 секунды от funding
                if abs(ms_to_funding) <= 2000:
                    rate = float(d["r"])
                    log(f"[MARKPRICE] rate={rate:.4%}, nextFunding={entry_T}, ms_to_funding={ms_to_funding}")

                # Входим в позицию прямо перед funding
                if not entered and 0 < ms_to_funding <= CHECK_WINDOW_MS:
                    delay = max(0, (entry_T - ENTRY_OFFSET_MS - now) / 1000)
                    log(f"[READY] entry in {delay*1000:.0f} ms")
                    await asyncio.sleep(delay)
                    log(f"[ENTRY] MARKET BUY {QTY}")
                    await market_order(session, "BUY", QTY)
                    entered = True

                # После начисления funding сразу закрываем сделку
                if entered and funding_event.is_set():
                    funding_event.clear()
                    pos = await current_pos(session)
                    if pos != 0:
                        side = "SELL" if pos > 0 else "BUY"
                        await market_order(session, side, abs(pos), reduce=True)
                        log(f"[EXIT] Position closed immediately after funding")
                    entered = False

                await asyncio.sleep(0.1)

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log("Stopped manually.")
