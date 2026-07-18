FROM python:3.10-slim
WORKDIR /app

# System dependencies for Prophet and CmdStanPy
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pandas numpy cmdstanpy==1.2.0

# Download and compile the engine
RUN install_cmdstan -d /opt/cmdstan --version 2.33.1

ENV CMDSTAN=/opt/cmdstan/cmdstan-2.33.1

RUN pip install --no-cache-dir prophet==1.1.5

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000
CMD ["uvicorn", "inference.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]