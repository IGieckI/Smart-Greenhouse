#!/bin/bash


CONTAINER=$1


docker compose down -v ${CONTAINER}
docker compose up -d --build --force-recreate ${CONTAINER}
docker compose logs -f ${CONTAINER}
