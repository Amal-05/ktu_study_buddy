import os

import uvicorn

from .db import ROOT


if __name__ == "__main__":
    uvicorn.run("backend.server:app", host="127.0.0.1", port=int(os.getenv("BUDDY_PORT", "8000")),
                app_dir=str(ROOT), access_log=False)
