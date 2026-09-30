FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY priorauth priorauth
COPY data/cases.json data/labels.json data/
ENV PRIORAUTH_DB_URL=sqlite:////tmp/priorauth.sqlite3
EXPOSE 8920
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8920/health')"
CMD ["uvicorn", "priorauth.api:app", "--host", "0.0.0.0", "--port", "8920"]
