FROM python:3.10-slim
WORKDIR /app

# 1. DIPENDENZE DI SISTEMA (Senza queste, il C++ non si compila)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 2. INSTALLA SOLO IL MOTORE E LE SUE DIPENDENZE BASE
RUN pip install --no-cache-dir pandas numpy cmdstanpy==1.2.0

# 3. SCARICA E COMPILA IL MOTORE IN UNA CARTELLA FISSA E ASSOLUTA
# In questo modo sappiamo ESATTAMENTE dove si trova il makefile
RUN install_cmdstan -d /opt/cmdstan

# 4. DICHIARA LA VARIABILE D'AMBIENTE (Così Prophet sa dove guardare)
ENV CMDSTAN=/opt/cmdstan/cmdstan-2.33.1

# 5. ORA INSTALLA PROPHET (Troverà la variabile CMDSTAN e userà il motore già pronto)
RUN pip install --no-cache-dir prophet==1.1.5

# 6. INSTALLA IL RESTO DEL TUO PROGETTO
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt