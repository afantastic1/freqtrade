import os
import ccxt

exchange = ccxt.binance({
    "apiKey": '9ophdwArJuDeMJaFevyY1qVAH7f7dPcQlnIMXpQP7tVNAJsPdntKTYTQT6zD5xz4',
    "secret": 'Ro4N4xDjcDZVtyXKf21cfu8BKIPfaOuLVzFexwBjx9vwiyDBYBSr1p2cNk3S4DvM',
    "enableRateLimit": True,
    "options": {
        "defaultType": "future"
    },
    # 如需要，使用实际可用的 HTTP 代理：
    "httpsProxy": "http://127.0.0.1:7890",
})

balance = exchange.fetch_balance()

usdt = balance.get("USDT", {})

print("Binance 合约账户")
print("可用余额:", usdt.get("free"))
print("已占用余额:", usdt.get("used"))
print("总余额:", usdt.get("total"))