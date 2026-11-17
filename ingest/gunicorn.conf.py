import multiprocessing
import os
from pathlib import Path

from beacon.registry import Registry

bind = "0.0.0.0:8080"
worker_class = "aiohttp.GunicornWebWorker"
workers = multiprocessing.cpu_count()
# The control socket is unused; Compose restarts the service.
control_socket_disable = True


def on_starting(server: object) -> None:
    Registry.load(Path(os.environ["BEACON_REGISTRY"]))
