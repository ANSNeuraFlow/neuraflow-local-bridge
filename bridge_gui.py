import asyncio
import csv
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import customtkinter as ctk

# ─── Konfiguracja ────────────────────────────────────────────────────────────

WS_HOST = "127.0.0.1"
WS_PORT = 8765
LSL_STREAM_NAME = "obci_eeg1"
N_CHANNELS = 8
OUT_DIR = "recordings"

os.makedirs(OUT_DIR, exist_ok=True)

# ─── Stan sesji ───────────────────────────────────────────────────────────────

@dataclass
class SessionState:
    session_id: Optional[str] = None
    current_marker: Optional[str] = None
    current_trial: Optional[int] = None
    running: bool = False
    samples_received: int = 0
    bytes_written: int = 0
    connected_clients: int = 0
    lsl_connected: bool = False
    ws_running: bool = False
    start_time: Optional[float] = None

state = SessionState()
sample_queue: "queue.Queue" = queue.Queue()
log_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()  # (level, message)


def log(level: str, msg: str):
    """Dodaje wiadomość do kolejki logów (bezpieczne z wątków)."""
    ts = datetime.now().strftime("%H:%M:%S")
    log_queue.put((level, f"[{ts}] {msg}"))


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
                if 'inlet' in locals():
                    inlet.close_stream()
            except Exception:
                pass
            time.sleep(2)


def _demo_lsl_thread():
    """Generuje losowe dane EEG gdy pylsl nie jest dostępne."""
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
                fname = f"{OUT_DIR}/session_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
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
        state.bytes_written += N_CHANNELS * 8 + 50  # aproksymacja

        if time.time() - last_flush > 1.0:
            current_file.flush()
            last_flush = time.time()


# ─── WebSocket handler ────────────────────────────────────────────────────────

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
                state.running = True
                state.start_time = time.time()
                log("ok", f"SESSION_START → ID: {state.session_id}")

            elif mtype == "MARKER":
                marker = msg.get("marker")
                state.current_marker = None if marker in (None, "", "NONE") else marker
                if "trialIndex" in msg:
                    state.current_trial = msg["trialIndex"]
                log("info", f"MARKER: {state.current_marker}  trial: {state.current_trial}")

            elif mtype in ("SESSION_END", "SESSION_ABORTED"):
                log("warn", f"Koniec sesji: {mtype}")
                state.current_marker = None
                state.session_id = None
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


def start_backend():
    """Uruchamia wszystkie wątki i pętle async."""
    threading.Thread(target=lsl_reader_thread, daemon=True).start()
    threading.Thread(target=writer_thread, daemon=True).start()

    def run_ws():
        asyncio.run(ws_main())

    threading.Thread(target=run_ws, daemon=True).start()


# ─── GUI ──────────────────────────────────────────────────────────────────────

DARK_BG    = "#0b0d11"
PANEL_BG   = "#11141d"
CARD_BG    = "#181c27"
ACCENT     = "#3b82f6"
ACCENT2    = "#5280ea"
GREEN      = "#84cc16"
YELLOW     = "#ff6a00"
RED        = "#dc2626"
TEXT       = "#ffffff"
TEXT_DIM   = "#9ca3af"
BORDER     = "#262c3d"

FONT_MONO  = ("JetBrains Mono", 11)
FONT_MONO_SM = ("JetBrains Mono", 10)
FONT_LABEL = ("Roboto", 12)
FONT_TITLE = ("Roboto", 22, "bold")
FONT_NUM   = ("Roboto", 28, "bold")
FONT_NUM_SM = ("Roboto", 18, "bold")


class StatusDot(ctk.CTkCanvas):
    """Animowana kropka statusu."""
    def __init__(self, master, size=10, **kwargs):
        super().__init__(master, width=size, height=size,
                         bg=CARD_BG, highlightthickness=0, **kwargs)
        self._size = size
        self._color = TEXT_DIM
        self._anim_job = None
        self._alpha = 255
        self._direction = -1
        self.draw()

    def draw(self):
        self.delete("all")
        r = self._size // 2
        self.create_oval(1, 1, self._size - 1, self._size - 1,
                         fill=self._color, outline="")

    def set_color(self, color: str, pulse: bool = False):
        self._color = color
        if self._anim_job:
            self.after_cancel(self._anim_job)
            self._anim_job = None
        self.draw()
        if pulse:
            self._pulse()

    def _pulse(self):
        # Prosta animacja: re-rysuj z lekkim przesunięciem
        self.draw()
        self._anim_job = self.after(800, self._pulse)


class StatCard(ctk.CTkFrame):
    """Karta ze statystyką."""
    def __init__(self, master, label: str, unit: str = "", **kwargs):
        super().__init__(master, fg_color=CARD_BG, corner_radius=8,
                         border_width=1, border_color=BORDER, **kwargs)
        self._var = ctk.StringVar(value="—")
        ctk.CTkLabel(self, text=label, font=ctk.CTkFont("Roboto", 10, "bold"),
                     text_color=TEXT_DIM).pack(pady=(8, 1))
        val = ctk.CTkLabel(self, textvariable=self._var,
                           font=ctk.CTkFont("Roboto", 20, "bold"),
                           text_color=ACCENT)
        if unit:
            val.pack()
            ctk.CTkLabel(self, text=unit, font=ctk.CTkFont("Roboto", 9),
                         text_color=TEXT_DIM).pack(pady=(0, 6))
        else:
            val.pack(pady=(0, 6))

    def set(self, value):
        self._var.set(str(value))


class LogPanel(ctk.CTkFrame):
    """Panel logów z kolorowaniem poziomów."""
    COLORS = {
        "ok":    GREEN,
        "warn":  YELLOW,
        "error": RED,
        "info":  TEXT,
    }

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=CARD_BG, corner_radius=8,
                         border_width=1, border_color=BORDER, **kwargs)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(header, text="● SYSTEM LOG",
                     font=ctk.CTkFont("Roboto", 11, "bold"),
                     text_color=TEXT_DIM).pack(side="left")
        self._btn_clear = ctk.CTkButton(
            header, text="WYCZYŚĆ", width=70, height=24,
            font=ctk.CTkFont("Roboto", 10, "bold"),
            fg_color=CARD_BG, hover_color=BORDER,
            text_color=TEXT, corner_radius=4,
            border_width=1, border_color=BORDER,
            command=self._clear
        )
        self._btn_clear.pack(side="right")

        self._text = ctk.CTkTextbox(
            self, font=ctk.CTkFont("JetBrains Mono", 11),
            fg_color="#0a0c10", text_color=TEXT,
            corner_radius=8, wrap="word",
            activate_scrollbars=True,
        )
        self._text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._text.configure(state="disabled")

        # Tagi kolorów
        self._text._textbox.tag_configure("ok",    foreground=GREEN)
        self._text._textbox.tag_configure("warn",  foreground=YELLOW)
        self._text._textbox.tag_configure("error", foreground=RED)
        self._text._textbox.tag_configure("info",  foreground=TEXT)
        self._text._textbox.tag_configure("dim",   foreground=TEXT_DIM)

    def add(self, level: str, message: str):
        self._text.configure(state="normal")
        # Rozdziel timestamp od reszty
        if message.startswith("[") and "]" in message:
            ts_end = message.index("]") + 1
            ts = message[:ts_end]
            rest = message[ts_end:]
            self._text._textbox.insert("end", ts, "dim")
            self._text._textbox.insert("end", rest + "\n", level)
        else:
            self._text._textbox.insert("end", message + "\n", level)
        self._text._textbox.see("end")
        self._text.configure(state="disabled")

    def _clear(self):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")


class ConnectionRow(ctk.CTkFrame):
    """Wiersz z statusem połączenia."""
    def __init__(self, master, label: str, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._dot = StatusDot(self, size=12)
        self._dot.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(self, text=label,
                     font=ctk.CTkFont("Roboto", 12),
                     text_color=TEXT).pack(side="left")
        self._status_var = ctk.StringVar(value="rozłączony")
        self._status_lbl = ctk.CTkLabel(
            self, textvariable=self._status_var,
            font=ctk.CTkFont("Roboto", 12),
            text_color=TEXT_DIM
        )
        self._status_lbl.pack(side="right")

    def set_connected(self, connected: bool, text: str = ""):
        if connected:
            self._dot.set_color(GREEN, pulse=True)
            self._status_var.set(text or "połączony")
            self._status_lbl.configure(text_color=GREEN)
        else:
            self._dot.set_color(RED)
            self._status_var.set(text or "rozłączony")
            self._status_lbl.configure(text_color=RED)

    def set_waiting(self, text: str = "szukam..."):
        self._dot.set_color(YELLOW, pulse=True)
        self._status_var.set(text)
        self._status_lbl.configure(text_color=YELLOW)


class BCIBridgeApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("BCI Bridge")
        self.geometry("960x680")
        self.minsize(800, 580)
        self.configure(fg_color=DARK_BG)

        self._build_ui()
        self._start_update_loop()

    # ── Budowanie UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Nagłówek ─────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=0, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        left_h = ctk.CTkFrame(header, fg_color="transparent")
        left_h.pack(side="left", padx=20, pady=12)

        ctk.CTkLabel(
            left_h, text="⬡  BCI BRIDGE",
            font=ctk.CTkFont("Roboto", 18, "bold"),
            text_color=ACCENT
        ).pack(side="left")

        ctk.CTkLabel(
            left_h, text="  v2.0  OpenBCI → LSL → WebSocket",
            font=ctk.CTkFont("Roboto", 12),
            text_color=TEXT_DIM
        ).pack(side="left", padx=(8, 0))

        right_h = ctk.CTkFrame(header, fg_color="transparent")
        right_h.pack(side="right", padx=20)

        self._session_dot = StatusDot(right_h, size=10)
        self._session_dot.pack(side="left", padx=(0, 6))
        self._session_label = ctk.CTkLabel(
            right_h, text="brak sesji",
            font=ctk.CTkFont("Roboto", 12),
            text_color=TEXT_DIM
        )
        self._session_label.pack(side="left")

        # ── Główny layout ─────────────────────────────────────────────────────
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=12)

        left_col = ctk.CTkFrame(main, fg_color="transparent", width=300)
        left_col.pack(side="left", fill="y", padx=(0, 10))
        left_col.pack_propagate(False)

        right_col = ctk.CTkFrame(main, fg_color="transparent")
        right_col.pack(side="left", fill="both", expand=True)

        # ── Lewa kolumna: status połączeń ─────────────────────────────────────
        conn_card = ctk.CTkFrame(left_col, fg_color=CARD_BG, corner_radius=8,
                                  border_width=1, border_color=BORDER)
        conn_card.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(conn_card, text="POŁĄCZENIA",
                     font=ctk.CTkFont("Roboto", 11, "bold"),
                     text_color=TEXT_DIM).pack(anchor="w", padx=14, pady=(12, 8))

        self._lsl_row = ConnectionRow(conn_card, "LSL / OpenBCI")
        self._lsl_row.pack(fill="x", padx=14, pady=4)

        sep = ctk.CTkFrame(conn_card, height=1, fg_color=BORDER)
        sep.pack(fill="x", padx=14, pady=6)

        self._ws_row = ConnectionRow(conn_card, "WebSocket Server")
        self._ws_row.pack(fill="x", padx=14, pady=(0, 6))

        sep2 = ctk.CTkFrame(conn_card, height=1, fg_color=BORDER)
        sep2.pack(fill="x", padx=14, pady=4)

        self._client_row = ConnectionRow(conn_card, "Frontend Client")
        self._client_row.pack(fill="x", padx=14, pady=(0, 12))

        # ── Statystyki ────────────────────────────────────────────────────────
        stats_label = ctk.CTkLabel(left_col, text="STATYSTYKI",
                                    font=ctk.CTkFont("Roboto", 11, "bold"),
                                    text_color=TEXT_DIM)
        stats_label.pack(anchor="w", pady=(4, 6))

        stats_col = ctk.CTkFrame(left_col, fg_color="transparent")
        stats_col.pack(fill="x")

        self._stat_session = StatCard(stats_col, "CZAS SESJI", "")
        self._stat_session.pack(fill="x", pady=(0, 5))

        self._stat_marker = StatCard(stats_col, "MARKER", "")
        self._stat_marker.pack(fill="x", pady=(0, 0))

        # ── Konfiguracja (collapsible) ─────────────────────────────────────────
        cfg_frame = ctk.CTkFrame(left_col, fg_color=CARD_BG, corner_radius=8,
                                  border_width=1, border_color=BORDER)
        cfg_frame.pack(fill="x", pady=(10, 0))

        ctk.CTkLabel(cfg_frame, text="KONFIGURACJA",
                     font=ctk.CTkFont("Roboto", 11, "bold"),
                     text_color=TEXT_DIM).pack(anchor="w", padx=14, pady=(10, 6))

        def cfg_row(parent, label, default):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(row, text=label, width=110,
                         font=ctk.CTkFont("Roboto", 11),
                         text_color=TEXT_DIM, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, placeholder_text=default,
                                  font=ctk.CTkFont("Roboto", 11),
                                  fg_color="#0a0c10", border_color=BORDER,
                                  text_color=TEXT, height=28)
            entry.insert(0, default)
            entry.pack(side="left", fill="x", expand=True)
            return entry

        self._cfg_host = cfg_row(cfg_frame, "WS Host", WS_HOST)
        self._cfg_port = cfg_row(cfg_frame, "WS Port", str(WS_PORT))
        self._cfg_lsl  = cfg_row(cfg_frame, "LSL Stream", LSL_STREAM_NAME)
        ctk.CTkLabel(cfg_frame, text="⚠ zmiana wymaga restartu",
                     font=ctk.CTkFont("Roboto", 10),
                     text_color=TEXT_DIM).pack(pady=(2, 10))

        # ── Prawa kolumna: logi ───────────────────────────────────────────────
        self._log_panel = LogPanel(right_col)
        self._log_panel.pack(fill="both", expand=True)

        # ── Pasek na dole ─────────────────────────────────────────────────────
        footer = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=0, height=48)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        footer_inner = ctk.CTkFrame(footer, fg_color="transparent")
        footer_inner.pack(fill="both", expand=True, padx=16)

        ctk.CTkLabel(footer_inner,
                     text=f"  ws://{WS_HOST}:{WS_PORT}  •  {LSL_STREAM_NAME}  •  {N_CHANNELS}ch",
                     font=ctk.CTkFont("Roboto", 11),
                     text_color=TEXT_DIM).pack(side="left", pady=12)

        ctk.CTkButton(
            footer_inner, text="OTWÓRZ FOLDER", width=120, height=32,
            font=ctk.CTkFont("Roboto", 11, "bold"),
            fg_color=ACCENT, hover_color="#2563eb",
            text_color=TEXT, corner_radius=4,
            command=self._open_recordings
        ).pack(side="right", pady=8)

    # ── Pętla aktualizacji UI ─────────────────────────────────────────────────

    def _start_update_loop(self):
        self._update_tick()

    def _update_tick(self):
        # Logi
        while not log_queue.empty():
            try:
                level, msg = log_queue.get_nowait()
                self._log_panel.add(level, msg)
            except queue.Empty:
                break

        # Połączenia
        if state.lsl_connected:
            self._lsl_row.set_connected(True, f"ok  ({N_CHANNELS}ch)")
        else:
            self._lsl_row.set_waiting("szukam...")

        if state.ws_running:
            self._ws_row.set_connected(True, f"ws://{WS_HOST}:{WS_PORT}")
        else:
            self._ws_row.set_waiting("uruchamiam...")

        if state.connected_clients > 0:
            self._client_row.set_connected(True, f"{state.connected_clients} klientów")
        else:
            self._client_row.set_connected(False, "brak")

        # Sesja
        if state.running and state.session_id:
            self._session_dot.set_color(GREEN, pulse=True)
            short_id = state.session_id[-12:] if len(state.session_id) > 12 else state.session_id
            self._session_label.configure(text=f"● sesja: {short_id}", text_color=GREEN)
        else:
            self._session_dot.set_color(TEXT_DIM)
            self._session_label.configure(text="brak sesji", text_color=TEXT_DIM)

        if state.running and state.start_time is not None:
            elapsed = int(time.time() - state.start_time)
            m, s = divmod(elapsed, 60)
            h, m = divmod(m, 60)
            if h:
                self._stat_session.set(f"{h}:{m:02d}:{s:02d}")
            else:
                self._stat_session.set(f"{m}:{s:02d}")
        else:
            self._stat_session.set("—")

        mk = state.current_marker
        self._stat_marker.set(mk if mk else "—")

        self.after(1000, self._update_tick)

    # ── Akcje ─────────────────────────────────────────────────────────────────

    def _open_recordings(self):
        import subprocess, sys
        path = os.path.abspath(OUT_DIR)
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    log("info", "BCI Bridge GUI uruchamiany...")
    start_backend()
    app = BCIBridgeApp()
    app.mainloop()


if __name__ == "__main__":
    main()
