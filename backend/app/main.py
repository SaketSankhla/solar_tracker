"""Main application entry point for the Solar Tracker backend."""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db
from app.config import get_settings
from app.api.routes import router, ws_manager
from app.mqtt import MqttClient
from app.workers import telemetry_queue

settings = get_settings()
mqtt_client = MqttClient(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle for the FastAPI application."""
    # 1. Initialise database (create tables if needed)
    await init_db()

    # 2. Attach shared objects to app state so routes can access them
    app.state.mqtt_client = mqtt_client
    app.state.ws_manager = ws_manager

    # 3. Start background worker that consumes the telemetry queue
    queue_task = asyncio.create_task(telemetry_queue.run())

    # 4. Start MQTT client (connects to broker, subscribes, dispatches)
    await mqtt_client.start()

    yield

    # --- Shutdown ---
    await mqtt_client.stop()
    queue_task.cancel()
    try:
        await queue_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Solar Tracker Backend API",
    description=(
        "Backend services for the AI-Enabled Smart Solar Tracking "
        "and Energy Monitoring System."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Restrict CORS to the local Vite dev server.
# Update origins for production/static builds as needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(router)


@app.get("/")
async def root():
    """Serves the simple single-page dashboard directly."""
    index_file = STATIC_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return {"status": "ok", "app": "Solar Tracker API", "version": "0.1.0"}


@app.get("/api/ping")
async def ping():
    return {"status": "ok", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
