import os

# ─── Sieć ─────────────────────────────────────────────────────────────────────

WS_HOST = "127.0.0.1"
WS_PORT = 8765

# ─── LSL ──────────────────────────────────────────────────────────────────────

LSL_STREAM_NAME = "obci_eeg1"
N_CHANNELS = 8

# ─── Pliki ────────────────────────────────────────────────────────────────────

OUT_DIR = "recordings"
os.makedirs(OUT_DIR, exist_ok=True)

# ─── Kolory ────────────────────────────────────────────────────────────────────

SURFACE             = "#0b0d11"  
SURFACE_CONTAINER   = "#14181d"  
SURFACE_INVERTED    = "#e7e7e7"  
ON_SURFACE          = "#ffffff"
ON_SURFACE_DIM      = "#b3b3b3" 
ON_SURFACE_INVERTED = "#000000"
ACCENT              = "#3b82f6" 
ACCENT_DIM          = "#234e94" 
SUCCESS             = "#84cc16"
WARNING             = "#ff6a00"
ERROR               = "#dc2626"
BORDER_SUBTLE       = "#2a3138"

# ─── Aliasy ────────────────────────────────────────────────────────────────────

BG       = SURFACE
PANEL    = SURFACE
CARD_BG  = SURFACE_CONTAINER
CARD2    = SURFACE_CONTAINER
TEXT     = ON_SURFACE
TEXT_DIM = ON_SURFACE_DIM
BORDER   = BORDER_SUBTLE

# ─── Przyciski ─────────────────────────────────────────────────────────────────

BTN_RADIUS            = 4
BTN_SECONDARY_FG      = SURFACE_CONTAINER
BTN_SECONDARY_HOVER   = "#181d24"
BTN_INVERSE_FG        = SURFACE_INVERTED
BTN_INVERSE_HOVER     = "#d9d9d9"
BTN_SECONDARY_BORDER  = BORDER_SUBTLE

FONT_FAMILY = "Roboto"
