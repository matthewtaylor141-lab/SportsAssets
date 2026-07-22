.PHONY: dev up down migrate test lint seed-roster

dev: up

up:
	docker compose up --build

down:
	docker compose down

migrate:
	docker compose run --rm migrate

test:
	cd backend && python -m pytest -q

seed-roster:
	docker compose run --rm api python -m sportsassets.scripts.seed_roster
