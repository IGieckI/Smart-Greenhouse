FROM node:18-alpine
WORKDIR /app


RUN apk update && \
    apk add --no-cache alsa-utils alsa-lib mpg123

    
COPY package*.json ./
RUN npm install

EXPOSE 3001
CMD ["node", "--watch", "server.js"]