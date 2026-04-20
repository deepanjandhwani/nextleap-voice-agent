import uvicorn

from advisor_scheduler.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(
        "advisor_scheduler.api.app:app",
        host=s.api_host,
        port=s.api_port,
        reload=False,
    )
