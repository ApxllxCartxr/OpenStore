"""Entry point for: python -m merchant_agent"""

import uvicorn

from merchant_agent.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "merchant_agent.app:app",
        host=settings.a2a_host,
        port=settings.a2a_port,
        reload=False,
    )
