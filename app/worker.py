"""Run the email worker as its own process:  python -m app.worker
(Useful on a paid Render 'Background Worker'. On the free plan the API process runs the worker thread itself.)"""
import logging
import time

from app.config import get_settings
from app.services.email_worker import process_batch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

if __name__ == "__main__":
    settings = get_settings()
    while True:
        if process_batch() == 0:
            time.sleep(settings.email_poll_seconds)
