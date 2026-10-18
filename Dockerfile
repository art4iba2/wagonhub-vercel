FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml ./
COPY wagonhub ./wagonhub
RUN pip install --no-cache-dir .
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
USER app
EXPOSE 8000
CMD ["uvicorn", "wagonhub.app:app", "--host", "0.0.0.0", "--port", "8000"]
