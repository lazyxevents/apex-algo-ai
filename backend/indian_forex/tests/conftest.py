import sys
from pathlib import Path

FOREX_SRC = Path(__file__).resolve().parents[1] / "src"
if str(FOREX_SRC) not in sys.path:
    sys.path.insert(0, str(FOREX_SRC))
