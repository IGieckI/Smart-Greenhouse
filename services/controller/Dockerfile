FROM node:18-alpine
WORKDIR /app

# Copia solo i manifesti per installare le dipendenze
COPY package*.json ./
RUN npm install

# Nessuna COPY . . ! Il codice verrà montato dal docker-compose

EXPOSE 3001
CMD ["node", "--watch", "server.js"]