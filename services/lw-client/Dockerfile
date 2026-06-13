FROM python:3.10-slim
WORKDIR /app

# Assicurati che in queste 4 cartelle il file si chiami esattamente "requirements.txt"
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Nessuna COPY . . ! Il codice verrà montato dal docker-compose