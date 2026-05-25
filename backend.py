import asyncio
import csv
import json
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from config import WS_HOST, WS_PORT, LSL_STREAM_NAME, N_CHANNELS, OUT_DIR

# Markery prób (BCI) — REST i inne pomocnicze nie wchodzą do licznika.
COUNTABLE_MARKERS = frozenset({
    "LEFT_HAND",
    "RIGHT_HAND",
    "BOTH_HANDS",
    "FEET",
})

# ─── Kolejki ──────────────────────────────────────────────────────────────────

sample_queue: "queue.Queue" = queue.Queue()
log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()


def log(level: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    log_queue.put((level, f"[{ts}] {msg}"))


# ─── Stan sesji ───────────────────────────────────────────────────────────────

@dataclass
class SessionState:
    session_id: Optional[str] = None
    current_marker: Optional[str] = None
    current_trial: Optional[int] = None
    session_marker_count: int = 0
    running: bool = False
    samples_received: int = 0
    bytes_written: int = 0
    connected_clients: int = 0
    lsl_connected: bool = False
    ws_running: bool = False
    start_time: Optional[float] = None


state = SessionState()


# ─── Wątek LSL ────────────────────────────────────────────────────────────────

def lsl_reader_thread():
    try:
        from pylsl import StreamInlet, resolve_byprop
    except ImportError:
        log("error", "pylsl nie zainstalowane — tryb demo (losowe dane)")
        _demo_lsl_thread()
        return

    while True:
        log("info", f"Szukam strumienia LSL: {LSL_STREAM_NAME}")
        state.lsl_connected = False
        streams = resolve_byprop("name", LSL_STREAM_NAME, timeout=5)
        if not streams:
            log("warn", "Nie znaleziono strumienia LSL. Sprawdź OpenBCI GUI > Networking > LSL")
            time.sleep(3)
            continue

        try:
            inlet = StreamInlet(streams[0], max_chunklen=32, recover=False)
            state.lsl_connected = True
            log("ok", f"Połączono z LSL: {streams[0].name()}")

            while True:
                sample, ts = inlet.pull_sample(timeout=1.0)
                if sample is None or len(sample) == 0:
                    continue
                if len(sample) < N_CHANNELS:
                    continue
                state.samples_received += 1
                sample_queue.put((
                    ts,
                    sample[:N_CHANNELS],
                    state.current_marker,
                    state.current_trial,
                    state.session_id,
                ))
        except Exception as e:
            state.lsl_connected = False
            log("error", f"Rozłączono z LSL: {e}. Ponowne połączenie za 2s...")
            try:
                if "inlet" in locals():
                    inlet.close_stream()
            except Exception:
                pass
            time.sleep(2)


def _demo_lsl_thread():
    import random
    log("info", "Tryb DEMO — generuję syntetyczne dane EEG (250 Hz)")
    state.lsl_connected = True
    interval = 1.0 / 250.0
    while True:
        sample = [random.gauss(0, 10) for _ in range(N_CHANNELS)]
        state.samples_received += 1
        sample_queue.put((
            time.time(),
            sample,
            state.current_marker,
            state.current_trial,
            state.session_id,
        ))
        time.sleep(interval)


# ─── Wątek zapisu CSV ─────────────────────────────────────────────────────────

def writer_thread():
    current_file = None
    writer = None
    open_session = None
    last_flush = time.time()

    while True:
        try:
            ts, ch, marker, trial, session_id = sample_queue.get(timeout=1.0)
        except queue.Empty:
            if writer is not None and current_file is not None:
                current_file.flush()
            continue

        if session_id != open_session:
            if current_file:
                current_file.close()
                log("info", "Plik sesji zamknięty")
            open_session = session_id
            if session_id:
                fname = (
                    f"{OUT_DIR}/session_{session_id}_"
                    f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                )
                current_file = open(fname, "w", newline="")
                writer = csv.writer(current_file)
                writer.writerow([
                    "lsl_ts", "recv_ts",
                    *[f"ch{i+1}" for i in range(N_CHANNELS)],
                    "marker", "trial_index", "session_id",
                ])
                log("ok", f"Nagrywanie → {fname}")
            else:
                current_file = None
                writer = None

        if writer is None:
            continue

        writer.writerow([
            ts, time.time(),
            *ch,
            marker or "",
            trial if trial is not None else "",
            session_id or "",
        ])
        state.bytes_written += N_CHANNELS * 8 + 50

        if time.time() - last_flush > 1.0:
            current_file.flush()
            last_flush = time.time()


# ─── WebSocket ────────────────────────────────────────────────────────────────

async def handle_front(websocket):
    state.connected_clients += 1
    log("ok", f"Klient WebSocket połączony ({state.connected_clients} aktywnych)")
    try:
        async for message in websocket:
            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")

            if mtype == "SESSION_START":
                state.session_id = msg.get("sessionId") or f"local-{int(time.time())}"
                state.current_marker = None
                state.current_trial = None
                state.session_marker_count = 0
                state.running = True
                state.start_time = time.time()
                log("ok", f"SESSION_START → ID: {state.session_id}")

            elif mtype == "MARKER":
                marker = msg.get("marker")
                state.current_marker = None if marker in (None, "", "NONE") else marker
                if "trialIndex" in msg:
                    state.current_trial = msg["trialIndex"]
                if state.current_marker and state.current_marker.upper() in COUNTABLE_MARKERS:
                    state.session_marker_count += 1
                log("info", f"MARKER: {state.current_marker}  trial: {state.current_trial}")

            elif mtype in ("SESSION_END", "SESSION_ABORTED"):
                log("warn", f"Koniec sesji: {mtype}")
                state.current_marker = None
                state.session_id = None
                state.session_marker_count = 0
                state.running = False
                state.start_time = None

    except Exception:
        pass
    finally:
        state.connected_clients = max(0, state.connected_clients - 1)
        log("warn", f"Klient WebSocket rozłączony ({state.connected_clients} aktywnych)")


async def ws_main():
    try:
        import websockets
        state.ws_running = True
        log("ok", f"WebSocket nasłuchuje na ws://{WS_HOST}:{WS_PORT}")
        async with websockets.serve(handle_front, WS_HOST, WS_PORT):
            await asyncio.Future()
    except ImportError:
        log("error", "websockets nie zainstalowane")
    except OSError as e:
        log("error", f"Nie można uruchomić WebSocket: {e}")
        state.ws_running = False


# ─── Start ────────────────────────────────────────────────────────────────────

def start_backend():
    threading.Thread(target=lsl_reader_thread, daemon=True).start()
    threading.Thread(target=writer_thread, daemon=True).start()

    def _run_ws():
        asyncio.run(ws_main())

    threading.Thread(target=_run_ws, daemon=True).start()
