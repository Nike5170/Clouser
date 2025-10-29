import asyncio, aiohttp, json, time, hmac, hashlib
from urllib.parse import urlencode
from datetime import datetime

API_KEY    = "9TvpACxlJtkRD6s22omjR7DzoZaBMouRUgtNuZAsemjwr50SE0rHOfn1u742BAqV"
API_SECRET = "tv5mhBQCuQYWE8qfrmk7O7a7Wtq9bckZvgNgE29SEhGRb0L1998g3ktjpwJxZwi6"
SYMBOL     = "ENSOUSDT"
QTY        = 10
BASE       = "https://fapi.binance.com"
WS_MARK    = f"wss://fstream.binance.com/ws/{SYMBOL.lower()}@markPrice@1s"

ENTRY_OFFSET_MS = 100       # вход за 100 мс до funding
CHECK_WINDOW_MS = 30000    # проверяем только 30 секунд до события

# -------------------- ЛОГ --------------------
def log(msg):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}")

# -------------------- ПОДПИСЬ --------------------
def sign(params):
    q = urlencode(params)
    return hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()

# -------------------- ЗАПРОС --------------------
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

# -------------------- МАРКЕТ ОРДЕР --------------------
async def market_order(session, side, qty, reduce=False):
    p = {"symbol": SYMBOL, "side": side, "type": "MARKET", "quantity": str(qty)}
    if reduce:
        p["reduceOnly"] = "true"
    log(f"[SEND] MARKET {side} {qty} (reduceOnly={reduce})")
    res = await signed_req(session, "POST", "/fapi/v1/order", p)
    log(f"[ORDER] {side} {qty}: {res}")

# -------------------- ФАНДИНГ --------------------
async def funding_info(session):
    d = await signed_req(session, "GET", "/fapi/v1/premiumIndex", {"symbol": SYMBOL})
    funding_rate = float(d.get("lastFundingRate", 0))
    next_funding = int(d.get("nextFundingTime", 0))
    return funding_rate, next_funding

# -------------------- ОСНОВНОЙ ЦИКЛ --------------------
async def run():
    async with aiohttp.ClientSession() as session:
        rate, next_funding = await funding_info(session)
        next_dt = datetime.utcfromtimestamp(next_funding / 1000).strftime("%Y-%m-%d %H:%M:%S")
        log(f"[INFO] Next funding time: {next_dt}, funding rate: {rate:.6f}")

        async with session.ws_connect(WS_MARK) as ws:
            log(f"[WS] Connected markPrice for {SYMBOL}")
            entered = False
            entry_T = next_funding

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                d = json.loads(msg.data)

                now = int(time.time() * 1000)
                ms_to_funding = entry_T - now

                # лог только около funding
                if abs(ms_to_funding) <= 2000:
                    rate = float(d["r"])
                    log(f"[MARKPRICE] rate={rate:.4%}, nextFunding={entry_T}, ms_to_funding={ms_to_funding}")

                # вход за 50 мс до funding
                if not entered and 0 < ms_to_funding <= CHECK_WINDOW_MS:
                    delay = max(0, (entry_T - ENTRY_OFFSET_MS - now) / 1000)
                    log(f"[READY] entry in {delay*1000:.0f} ms")
                    await asyncio.sleep(delay)
                    log(f"[ENTRY] MARKET BUY {QTY}")
                    await market_order(session, "BUY", QTY)
                    entered = True

                # закрытие сразу после funding (как только ms_to_funding стал отрицательным)
                if entered and ms_to_funding < 0:
                    log(f"[CLOSE_TRIGGER] ms_to_funding={ms_to_funding} (<0) → отправляем reduceOnly SELL")
                    await market_order(session, "SELL", QTY, reduce=True)
                    log("[EXIT] Position closed immediately after funding (on first negative ms_to_funding)")
                    entered = False

                await asyncio.sleep(0.005)  # обновление каждые 5 мс

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log("Stopped manually.")
