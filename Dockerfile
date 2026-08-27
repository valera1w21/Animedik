FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY web ./web

# A separate user with no administrator rights.
# If a hole is found in the application, the attacker ends up an ordinary
# user inside the container rather than the owner of the system.
RUN useradd --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown -R app:app /app /data
USER app

ENV DB_PATH=/data/anime.db WEB_DIR=/app/web PORT=8000
EXPOSE 8000

# One worker process is not "we never got round to it", it is a condition of
# the work: guest passes and rate counters live in the process's memory. With
# a second process a guest would accidentally be let in one moment and not
# the next, while the limits would be halved. For a home site one process is
# plenty, with room to spare.
#
# --proxy-headers and --forwarded-allow-ips='*' have been removed on purpose.
# With them uvicorn rewrote the client's address from X-Forwarded-For itself,
# trusting that header from ANYONE, and request.client held whatever the
# visitor had sent. The address is parsed by the application (client_ip in
# api/main.py): it takes X-Real-IP, which nginx overwrites from scratch, and
# checks that it is an address at all. Two different parsings of one header
# are one extra way to go wrong; one is left, the one under our control.
#
# exec — so that uvicorn becomes the first process and receives the stop
# signal itself, rather than waiting to be finished off on a timeout.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
