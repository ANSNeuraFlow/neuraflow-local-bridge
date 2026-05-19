# NeuraFlow Local Bridge

Mała aplikacja desktopowa (CustomTkinter) łącząca źródło EEG przez **LSL** z frontendem przez **WebSocket** oraz nagrywająca próbki do plików **CSV**.

## Uruchamianie

**Wymagania:** Python 3.10+ (zwykle działa i na 3.9 po stronie środowiska).

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Opcjonalnie można użyć skryptów `setup.sh` i `run.sh` z repo (chmod + uruchomienie pod Unixem).

## Struktura kodu

| Plik           | Opis                                        |
|----------------|---------------------------------------------|
| `main.py`      | Punkt wejścia (GUI + backend)               |
| `gui.py`       | Widok (`NeuraFlow Local Bridge`)           |
| `backend.py`   | LSL, zapis CSV, serwer WebSocket            |
| `config.py`    | Stałe sieciowe, ścieżki, kolory GUI         |

Plik `bridge_gui.py` to starsza, jednoplikowa wersja z osobną konfiguracją w interfejsie — domyślnie używaj `main.py` + `gui.py`.

## Interfejs

W lewej kolumnie są m.in. statusy (LSL, WebSocket, klient) oraz dwa **kafelki** jeden pod drugim:

- **Czas sesji** — licznik od `SESSION_START` (format `m:ss` albo `h:mm:ss` po godzinie); bez aktywnej sesji pokazuje „—”.
- **Marker** — aktualna wartość z ostatniej wiadomości `MARKER`; brak markera: „—”.

Na dole: skrót adresu WebSocket / nazwy strumienia LSL i przycisk otwierający folder `recordings/`.

## Integracja ze sprzętem

1. Uruchom **OpenBCI GUI**, w Networking włącz **LSL**.
2. Domyślna nazwa strumienia: `obci_eeg1` (zmiana w [`config.py`](config.py): `LSL_STREAM_NAME`, `N_CHANNELS`).
3. Serwer nasłuchuje na `ws://127.0.0.1:8765` (host i port: `WS_HOST`, `WS_PORT` w [`config.py`](config.py)).

Bez pakietu `pylsl` backend przechodzi w tryb generowania pseudo-EEG (komunikat w logu); do prawdziwych próbek **zainstaluj `pylsl`**.

## Dane testowe bez OpenBCI

Syntetyczne próbki (ok. **250 Hz**, 8 kanałów) startują **tylko** wtedy, gdy import `pylsl` się nie udaje (np. brak pakietu). Implementacja: `backend.py` — `_demo_lsl_thread`. Nie ma przełącznika w GUI; źródło zależy wyłącznie od tego, czy `pylsl` jest dostępne.

## Nagrania

Pliki pojawiają się w katalogu `recordings/` (nie jest commitowany do Gita):

`session_<id>_<YYYYMMDD_HHMMSS>.csv`

Kolumny: `lsl_ts`, `recv_ts`, `ch1`…`ch8`, `marker`, `trial_index`, `session_id`.

## Wiadomości WebSocket (JSON z frontendu)

```json
{ "type": "SESSION_START", "sessionId": "abc123" }
{ "type": "MARKER", "marker": "LEFT", "trialIndex": 0 }
{ "type": "SESSION_END" }
```

Zdarzenie **SESSION_ABORTED** jest traktowane jak koniec nagrywania (jak `SESSION_END`).

## Zależności

Zobacz [`requirements.txt`](requirements.txt): `websockets`, `customtkinter`, `Pillow`, opcjonalnie `pylsl` dla rzeczywistego OpenBCI/LSL.
