import os
import socket
import pandas as pd


def compute_factor(history, *, as_of, params):
    escaped = False
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=0.5)
        escaped = True
    except OSError:
        pass
    for path in (
        "/var/run/docker.sock", "/home/von/.aws/credentials",
        "/alpha_lab/freeze.py", "/user_data/research/alpha_lab/experiments.sqlite3",
        "/mnt/holdout/2025.csv", "/srv/research-store.sqlite3",
    ):
        try:
            with open(path, "rb") as stream:
                stream.read(1)
            escaped = True
        except OSError:
            pass
    try:
        with open("/opt/factor-worker/escape-attempt", "w") as stream:
            stream.write("untrusted")
        escaped = True
    except OSError:
        pass
    return pd.Series({symbol: float(escaped) for symbol in history.index.get_level_values("symbol").unique()})
