from pathlib import Path
import sys

PACKAGE_PARENT = Path(__file__).resolve().parents[1]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from audio_recognition.scripts.run_regression_suite import *

if __name__ == "__main__":
    raise SystemExit(main())
