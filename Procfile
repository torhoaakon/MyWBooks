web: uvicorn mywbooks.api.app:app --host 0.0.0.0 --port 8000 --reload
worker: arq mywbooks.worker.WorkerSettings
redis:  redis-server --save "" --appendonly no
maintenance: uv run python -m mywbooks.maintenance.cleanup loop