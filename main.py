"""
NeuraFlow Local Bridge — punkt wejścia
"""

from backend import log, start_backend
from gui import App


def main():
    log("info", "NeuraFlow Local Bridge uruchamiany...")
    start_backend()
    App().mainloop()


if __name__ == "__main__":
    main()
