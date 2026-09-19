# Script: main.py
# Purpose: Start the backend, which opens the store, connects to the platform, runs the worker and serves the web API for the three screens
# Author: Jonas Lüthi
# Date: September 2026

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.service import RelayService
from app.webapi.routes import router









#### Step 1: Build the app ####

# Build the app around one service, which a test may hand in ready-made
def create_app(service = None, settings = None):
    if settings is None:
        settings = get_settings()



    # Start the service when the app starts and stop it when the app stops
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = service if service is not None else RelayService.build(settings)
        await app.state.service.start()
        try:
            yield
        finally:
            await app.state.service.stop()

    app = FastAPI(title = "Relay wallet control", version = "0.1.0", lifespan = lifespan)



    # Let the interface at the configured origins call the API
    origins = [origin.strip() for origin in settings.web_cors_origins.split(",") if origin.strip() != ""]
    app.add_middleware(CORSMiddleware, allow_origins = origins, allow_methods = ["*"], allow_headers = ["*"])
    app.include_router(router)



    # Answer the health check of this service
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "service": "relay-backend"}

    return app









#### Step 2: Expose the app ####

# Serve this object with uvicorn, for example: python -m uvicorn app.main:app --app-dir backend --port 8000
app = create_app()
