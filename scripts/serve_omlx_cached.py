"""Launch oMLX 0.6.3rc2 with cache blocks suited to this short-prefix workload.

Run using oMLX's own Python runtime, not the speech-asr environment.
This process-local adapter leaves the installed package untouched. Use ordinary
`omlx serve` to return to upstream behavior. Its private API is version guarded.
"""

import argparse
import logging
import sys
from functools import wraps


SUPPORTED_VERSION = "0.6.3rc2"


def configure_short_prefix_cache(block_size: int) -> None:
    from omlx import __version__
    from omlx.scheduler import Scheduler

    if __version__ != SUPPORTED_VERSION:
        raise RuntimeError(
            f"Cache adapter was verified with oMLX {SUPPORTED_VERSION}, found {__version__}. "
            "Use ordinary omlx serve until this adapter is verified for that version."
        )
    original = Scheduler._enlarge_block_size_for_arrays_cache

    @wraps(original)
    def configure(scheduler):
        original(scheduler)
        if not scheduler.config.paged_ssd_cache_dir:
            return
        if scheduler._detect_rotating_window_sizes():
            return
        model = scheduler.model
        if not hasattr(model, "make_cache"):
            return
        cache = model.make_cache()
        if not cache or not any(scheduler._cache_tree_has_arrays_cache(item) for item in cache):
            return
        # Match both chunk sizes to avoid an inconsistent GDN snapshot boundary.
        # Upstream uses 2048, which cannot cache this app's ~1600-token prefix.
        scheduler.config.paged_cache_block_size = block_size
        scheduler.config.prefill_step_size = block_size
        logging.getLogger("omlx.scheduler").info(
            "Project short-prefix adapter: ArraysCache block_size=%s, prefill_step_size=%s",
            block_size, block_size,
        )

    Scheduler._enlarge_block_size_for_arrays_cache = configure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--hybrid-cache-block-size", type=int, choices=(512, 1024, 2048), default=512)
    options, remaining = parser.parse_known_args()
    configure_short_prefix_cache(options.hybrid_cache_block_size)
    from omlx.cli import main as serve
    sys.argv = [sys.argv[0], "serve", *remaining]
    serve()


if __name__ == "__main__":
    main()
