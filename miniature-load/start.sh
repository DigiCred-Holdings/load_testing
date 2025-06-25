docker compose down -v
docker compose build master worker
docker compose up -d
tail -f stats/worker.log
