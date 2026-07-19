FROM python:3.10-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pandas numpy cmdstanpy==1.2.0 prophet==1.1.5

# RUN install_cmdstan --version 2.33.1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt