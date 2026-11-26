"""Run directly, or under fd-log-run DIRECTORY -- python this_file.py."""

import logging

from zig_fd_logging import Handler

logging.basicConfig(handlers=[Handler()], level=logging.DEBUG,
                    format="%(levelname)s:%(name)s:%(message)s")
logging.error("error %d", 17)
logging.warning("warning %s", "careful")
logging.info("info %d", 23)
logging.debug("debug %s", "abcd")
logging.getLogger("network").info("connected %d", 42)
