deploy:
	docker compose up -d

redeploy:
	docker compose down
	docker compose up -d
