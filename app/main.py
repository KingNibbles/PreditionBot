import logging, sys
from pathlib import Path

# lets this run as a script, as a module or from the exe
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config as C
from app.logging_config import setup_logging


def main():
    setup_logging(C.LOG_DIR)
    try:
        from app.ui import App
        App().mainloop()
    except Exception:
        logging.getLogger("main").exception("fatal")
        raise


if __name__ == "__main__":
    main()
