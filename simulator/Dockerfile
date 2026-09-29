FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY simulator ./simulator
WORKDIR /app/simulator
CMD ["python", "simulator.py"]
