.PHONY: up down test backend frontend
up:
	docker compose up --build

down:
	docker compose down

test:
	cd backend && pytest -q

backend:
	cd backend && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev
