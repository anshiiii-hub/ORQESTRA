FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONPATH=/srv
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY app ./app
COPY tests ./tests
COPY alembic.ini ./
COPY migrations ./migrations
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
