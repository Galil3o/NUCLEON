import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
