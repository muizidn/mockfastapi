.PHONY: deploy redeploy logs stop

deploy:
	docker compose up -d --build
	@echo "Available at http://localhost:4545"

redeploy:
	docker compose down
	docker compose up -d --build
	@echo "Available at http://localhost:4545"

stop:
	docker compose down

logs:
	docker compose logs -f
