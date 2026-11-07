"""Decision Diff: change analysis for decision-engine strategy versions.

Numeric libraries must not start helper threads in worker processes: sandboxes are forked from single-threaded
launchers only, so numeric thread pools are pinned to one thread and Arrow's jemalloc background thread is disabled before
those libraries are imported.
"""

import os

for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
os.environ.setdefault("JE_ARROW_MALLOC_CONF", "background_thread:false")
