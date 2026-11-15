"""Names whatever holds the agent still (13 Nov plan, A.9).

On 13 Nov the agent recorded nothing at all for ten seconds while a search and a watchlist pull
ran: no span from any thread, and every request around it took 5–7 s. Total silence across
threads is one thread keeping the interpreter, or the request loop, busy in one long call — and
nothing on record said which. Profiling the likely parsers found each of them fast.

So this watches, cheaply, and leaves evidence the next time it happens. A daemon thread pings the
request loop every half-second and arms `faulthandler` to dump **every thread's stack** if the
answer takes longer than `STALL_SECONDS`. `faulthandler`'s timer is a C thread that needs neither
the loop nor the interpreter lock, so it can write while whatever is stuck is still stuck — which
is the moment the stacks name the culprit. The dump goes to `data/logs/stalls.log`; the lag goes
to Logfire once the loop answers. Live runs only: demo mode and the tests make no such calls.
"""

import asyncio
import faulthandler
import threading
import time
from pathlib import Path

import logfire

PING_SECONDS = 0.5
STALL_SECONDS = 3.0
LOG = Path(__file__).resolve().parents[3] / "data" / "logs" / "stalls.log"


def start(loop: asyncio.AbstractEventLoop) -> threading.Event:
    """Starts the watchdog thread. Set the returned event to stop it."""
    stop = threading.Event()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=_watch, args=(loop, stop), name="loop-watchdog", daemon=True)
    thread.start()
    return stop


def _watch(loop: asyncio.AbstractEventLoop, stop: threading.Event) -> None:
    with LOG.open("a") as out:
        while not stop.wait(PING_SECONDS):
            answered = threading.Event()
            started = time.monotonic()
            faulthandler.dump_traceback_later(STALL_SECONDS, repeat=False, file=out)
            try:
                loop.call_soon_threadsafe(answered.set)
            except RuntimeError:
                faulthandler.cancel_dump_traceback_later()
                return  # the loop has closed: the app is stopping
            answered.wait(60)
            faulthandler.cancel_dump_traceback_later()
            lag = time.monotonic() - started
            if lag >= STALL_SECONDS:
                out.write(f"--- the request loop answered after {lag:.1f}s ({time.strftime('%Y-%m-%d %H:%M:%S')}) ---\n")
                out.flush()
                logfire.warn(
                    "The agent's request loop was held for {lag:.1f}s; every thread's stack is in data/logs/stalls.log",
                    lag=round(lag, 1),
                )
