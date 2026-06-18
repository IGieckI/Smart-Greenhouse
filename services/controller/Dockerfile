FROM node:18-alpine
WORKDIR /app

# Installa le dipendenze audio (ALSA per l'hardware e i player per wav/mp3)
RUN apk update && \
    apk add --no-cache alsa-utils alsa-lib mpg123

# Copia solo i manifesti per installare le dipendenze
COPY package*.json ./
RUN npm install

EXPOSE 3001
CMD ["node", "--watch", "server.js"]