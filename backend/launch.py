import os

import uvicorn

from .db import HOSTED, ROOT


if __name__ == "__main__":
    uvicorn.run("backend.server:app", host="0.0.0.0" if HOSTED else "127.0.0.1",
                port=int(os.getenv("BUDDY_PORT", "8000")), app_dir=str(ROOT), access_log=False,
                forwarded_allow_ips=os.getenv("BUDDY_PROXY_IPS", "127.0.0.1"), workers=1,
                limit_concurrency=48 if HOSTED else None, timeout_keep_alive=5)
