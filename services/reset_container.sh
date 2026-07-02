#!/bin/bash

#Pure utility bash file to reset config of a single container or the whole project

CONTAINER=$1

docker compose down -v ${CONTAINER}
docker compose up -d --build --force-recreate ${CONTAINER}
docker compose logs -f ${CONTAINER}
