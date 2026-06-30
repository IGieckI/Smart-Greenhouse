FROM python:3.10-slim
WORKDIR /app

# 1. Installa i compilatori essenziali (build-essential, curl) e le lib di sistema (libgomp1)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 2. Installa esplicitamente le dipendenze core per Prophet e Stan
RUN pip install --no-cache-dir pandas numpy prophet cmdstanpy

# 3. FORZA L'INSTALLAZIONE DEL MOTORE STAN (Cruciale per evitare crash silenziosi)
RUN python -c "import cmdstanpy; cmdstanpy.install_cmdstan()"

# 4. Copia e installa il resto delle dipendenze del progetto
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000
CMD ["uvicorn", "inference.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]