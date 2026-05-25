import os
import queue
import subprocess
import sys
import time

import customtkinter as ctk

from config import (
    BG, PANEL, CARD_BG,
    SURFACE_CONTAINER,
    ON_SURFACE, ON_SURFACE_DIM, ON_SURFACE_INVERTED,
    SUCCESS, WARNING, ERROR,
    BORDER_SUBTLE,
    BTN_RADIUS, BTN_SECONDARY_FG, BTN_SECONDARY_HOVER,
    BTN_INVERSE_FG, BTN_INVERSE_HOVER,
    BTN_SECONDARY_BORDER,
    FONT_FAMILY,
    WS_HOST, WS_PORT, LSL_STREAM_NAME, N_CHANNELS, OUT_DIR,
)
from backend import state, log, log_queue

_F = FONT_FAMILY

# Lekko ciemniejsze tło pola logów (~ głęboki surface)
LOG_INNER_BG = "#080a0f"

# ─── Komponenty ───────────────────────────────────────────────────────────────

class StatusPill(ctk.CTkFrame):
    def __init__(self, master, label: str, **kwargs):
        super().__init__(master, fg_color=CARD_BG, corner_radius=20,
                         border_width=1, border_color=BORDER_SUBTLE, **kwargs)
        self._dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(_F, 10),
                                  text_color=ON_SURFACE_DIM, width=14)
        self._dot.pack(side="left", padx=(10, 2), pady=6)
        ctk.CTkLabel(self, text=label, font=ctk.CTkFont(_F, 11),
                     text_color=ON_SURFACE).pack(side="left")
        self._val = ctk.CTkLabel(self, text="—", font=ctk.CTkFont(_F, 11),
                                  text_color=ON_SURFACE_DIM)
        self._val.pack(side="left", padx=(4, 12))

    def set_ok(self, text: str = "ok"):
        self._dot.configure(text_color=SUCCESS)
        self._val.configure(text=text, text_color=SUCCESS)

    def set_warn(self, text: str = "..."):
        self._dot.configure(text_color=WARNING)
        self._val.configure(text=text, text_color=WARNING)

    def set_error(self, text: str = "błąd"):
        self._dot.configure(text_color=ERROR)
        self._val.configure(text=text, text_color=ERROR)

    def set_idle(self, text: str = "—"):
        self._dot.configure(text_color=ON_SURFACE_DIM)
        self._val.configure(text=text, text_color=ON_SURFACE_DIM)


class StatCard(ctk.CTkFrame):

    def __init__(self, master, label: str, unit: str = "", **kwargs):
        super().__init__(master, fg_color=CARD_BG, corner_radius=12,
                         border_width=1, border_color=BORDER_SUBTLE, **kwargs)
        self._var = ctk.StringVar(value="—")
        ctk.CTkLabel(self, text=label, font=ctk.CTkFont(_F, 10, "bold"),
                     text_color=ON_SURFACE_DIM).pack(pady=(8, 1))
        val = ctk.CTkLabel(self, textvariable=self._var,
                           font=ctk.CTkFont(_F, 20, "bold"),
                           text_color=ON_SURFACE)
        if unit:
            val.pack()
            ctk.CTkLabel(self, text=unit, font=ctk.CTkFont(_F, 9),
                         text_color=ON_SURFACE_DIM).pack(pady=(0, 6))
        else:
            val.pack(pady=(0, 6))

    def set(self, value):
        self._var.set(str(value))


class LogPanel(ctk.CTkFrame):

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=CARD_BG, corner_radius=12,
                         border_width=1, border_color=BORDER_SUBTLE, **kwargs)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 6))

        ctk.CTkLabel(header, text="SYSTEM LOG",
                     font=ctk.CTkFont(_F, 11, "bold"),
                     text_color=ON_SURFACE_DIM).pack(side="left")

        ctk.CTkButton(
            header, text="Wyczyść", width=68, height=28,
            font=ctk.CTkFont(_F, 10),
            fg_color=BTN_SECONDARY_FG, hover_color=BTN_SECONDARY_HOVER,
            text_color=ON_SURFACE, corner_radius=BTN_RADIUS,
            border_width=1, border_color=BTN_SECONDARY_BORDER,
            command=self._clear,
        ).pack(side="right")

        self._text = ctk.CTkTextbox(
            self, font=ctk.CTkFont(_F, 11),
            fg_color=LOG_INNER_BG, text_color=ON_SURFACE,
            corner_radius=8, wrap="word",
            activate_scrollbars=True,
        )
        self._text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._text.configure(state="disabled")

        self._text._textbox.tag_configure("ok",    foreground=SUCCESS)
        self._text._textbox.tag_configure("warn",  foreground=WARNING)
        self._text._textbox.tag_configure("error", foreground=ERROR)
        self._text._textbox.tag_configure("info",  foreground=ON_SURFACE)
        self._text._textbox.tag_configure("dim",   foreground=ON_SURFACE_DIM)

    def add(self, level: str, message: str):
        self._text.configure(state="normal")
        if message.startswith("[") and "]" in message:
            ts_end = message.index("]") + 1
            self._text._textbox.insert("end", message[:ts_end], "dim")
            self._text._textbox.insert("end", message[ts_end:] + "\n", level)
        else:
            self._text._textbox.insert("end", message + "\n", level)
        self._text._textbox.see("end")
        self._text.configure(state="disabled")

    def _clear(self):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")


# ─── Główna aplikacja ─────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("NeuraFlow Local Bridge")
        self.geometry("960x660")
        self.minsize(780, 540)
        self.configure(fg_color=BG)

        self._build_ui()
        self._tick()

    # ── Budowanie UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=(10, 0))

        left = ctk.CTkFrame(main, fg_color="transparent", width=272)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)

        right = ctk.CTkFrame(main, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)

        self._build_left(left)
        self._build_right(right)
        self._build_footer()

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=58)
        header.pack(fill="x")
        header.pack_propagate(False)

        inner = ctk.CTkFrame(header, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20)

        ctk.CTkLabel(
            inner,
            text="NeuraFlow",
            font=ctk.CTkFont(_F, 17, "bold"),
            text_color=ON_SURFACE,
        ).pack(side="left", pady=16)

        ctk.CTkLabel(
            inner,
            text=" Local Bridge",
            font=ctk.CTkFont(_F, 17),
            text_color=ON_SURFACE_DIM,
        ).pack(side="left", pady=16)

    def _build_left(self, parent):
        # ── Status ────────────────────────────────────────────────────────────
        ctk.CTkLabel(parent, text="STATUS",
                     font=ctk.CTkFont(_F, 10, "bold"),
                     text_color=ON_SURFACE_DIM).pack(anchor="w", pady=(4, 6))

        self._frontend_pill = StatusPill(parent, "Klient frontendu")
        self._frontend_pill.pack(fill="x", pady=(0, 4))

        self._lsl_pill = StatusPill(parent, "LSL / OpenBCI")
        self._lsl_pill.pack(fill="x", pady=(0, 4))

        self._ws_pill = StatusPill(parent, "WebSocket")
        self._ws_pill.pack(fill="x", pady=(0, 16))

        # ── Statystyki ────────────────────────────────────────────────────────
        ctk.CTkLabel(parent, text="STATYSTYKI",
                     font=ctk.CTkFont(_F, 10, "bold"),
                     text_color=ON_SURFACE_DIM).pack(anchor="w", pady=(0, 6))

        stats_col = ctk.CTkFrame(parent, fg_color="transparent")
        stats_col.pack(fill="x", pady=(0, 16))

        self._stat_session = StatCard(stats_col, "CZAS SESJI", "")
        self._stat_session.pack(fill="x", pady=(0, 5))

        self._stat_marker = StatCard(stats_col, "MARKER", "")
        self._stat_marker.pack(fill="x", pady=(0, 5))

        self._stat_marker_count = StatCard(stats_col, "LICZNIK MARKERÓW", "")
        self._stat_marker_count.pack(fill="x", pady=(0, 0))

    def _build_right(self, parent):
        self._log_panel = LogPanel(parent)
        self._log_panel.pack(fill="both", expand=True)

    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, height=46)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        inner = ctk.CTkFrame(footer, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=16)

        ctk.CTkLabel(
            inner,
            text=f"ws://{WS_HOST}:{WS_PORT}  ·  {LSL_STREAM_NAME}  ·  {N_CHANNELS} kanałów",
            font=ctk.CTkFont(_F, 11),
            text_color=ON_SURFACE_DIM,
        ).pack(side="left", pady=12)

        # AppButton inverse (biały / surface-inverted) — jak przyciski w sesji BCI
        ctk.CTkButton(
            inner, text="Nagrania", width=100, height=32,
            font=ctk.CTkFont(_F, 11),
            fg_color=BTN_INVERSE_FG, hover_color=BTN_INVERSE_HOVER,
            text_color=ON_SURFACE_INVERTED, corner_radius=BTN_RADIUS,
            command=self._open_recordings,
        ).pack(side="right", pady=8)

    # ── Pętla aktualizacji ────────────────────────────────────────────────────

    def _tick(self):
        while not log_queue.empty():
            try:
                level, msg = log_queue.get_nowait()
                self._log_panel.add(level, msg)
            except queue.Empty:
                break

        # LSL status
        if state.lsl_connected:
            self._lsl_pill.set_ok(f"połączony  {N_CHANNELS}ch")
        else:
            self._lsl_pill.set_warn("szukam...")

        # WS status
        if state.ws_running:
            self._ws_pill.set_ok(f":{WS_PORT}")
        else:
            self._ws_pill.set_warn("uruchamianie...")

        # Klient frontendu (WebSocket spod aplikacji web)
        if state.connected_clients > 0:
            self._frontend_pill.set_ok("połączony")
        else:
            self._frontend_pill.set_idle("oczekuje…")

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

        if state.running:
            self._stat_marker_count.set(state.session_marker_count)
        else:
            self._stat_marker_count.set("—")

        self.after(1000, self._tick)

    # ── Akcje ─────────────────────────────────────────────────────────────────

    def _open_recordings(self):
        path = os.path.abspath(OUT_DIR)
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
