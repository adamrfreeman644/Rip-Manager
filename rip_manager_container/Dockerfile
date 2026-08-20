FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RIP_MANAGER_DATA=/data \
    RIP_MANAGER_CONFIG=/config

WORKDIR /app

# Requirements are copied first so the dependency layer is reused whenever only
# application code changes, which makes a forced reinstall noticeably quicker.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app
COPY simulator_node.py /app/simulator_node.py

EXPOSE 8080

# The host updater waits on this endpoint before declaring an update healthy,
# so the container reports readiness itself as well.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--no-server-header"]
