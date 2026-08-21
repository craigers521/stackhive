FROM python:3.12-slim AS builder

WORKDIR /build
RUN python3 -m ensurepip --default-pip
RUN python3 -m pip install --no-cache-dir --prefix=/install --upgrade pip

COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:$PATH" \
    FLASK_APP=app:create_app()

RUN groupadd -r stackhive && useradd -r -g stackhive -d /app stackhive

WORKDIR /app

COPY --from=builder /install /usr/local

COPY --chown=stackhive:stackhive . /app/

RUN mkdir -p /var/lib/stackhive /app/ansible && chown -R stackhive:stackhive /var/lib/stackhive /app/ansible

USER stackhive

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=4).status == 200 else 1)"

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "120", "app:create_app()"]
