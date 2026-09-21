# The library plus the web surface in web/, as one image.
#
#     docker build -t lit-storm/agents-research:dev .
#
# Python 3.13 rather than the host app's 3.14: openai-agents is the newest
# dependency here and 3.13 is what its wheels are known good on. It is its
# own container, so it does not have to match.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies before source, so editing a page does not reinstall them.
# Copied to distinct names on purpose: `COPY a b ./` flattens both into the
# directory, so the second file lands on top of the first.
COPY requirements.txt ./requirements.txt
COPY web/requirements.txt ./web-requirements.txt
RUN pip install -r requirements.txt -r web-requirements.txt

COPY . .
RUN pip install --no-deps -e .

RUN useradd --create-home --uid 10001 research
USER research

EXPOSE 3000

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:3000/healthz', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "3000"]
