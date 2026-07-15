# syntax=docker/dockerfile:1
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --system --uid 10001 monitor && chown -R monitor:monitor /app
USER monitor
EXPOSE 8080
CMD ["python", "-m", "replik_monitor.cli", "serve"]
