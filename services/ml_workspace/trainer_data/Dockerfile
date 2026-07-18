FROM python:3.10-slim
WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*


RUN pip install --no-cache-dir pandas numpy prophet==1.1.5

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt