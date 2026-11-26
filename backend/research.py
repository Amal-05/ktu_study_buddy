import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .core import get, put
from .db import Session

SEARCH_MOCK = os.getenv("SEARCH_MOCK", "1") == "1"
AGENT = "KTUStudyBuddy"


async def public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    try:
        found = await asyncio.get_running_loop().getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        return bool(found) and all(ipaddress.ip_address(item[4][0]).is_global for item in found)
    except (OSError, ValueError):
        return False


async def permitted(client, url):
    if not await public_url(url):
        return False
    parsed = urlsplit(url)
    robots_url = f"https://{parsed.netloc}/robots.txt"
    try:
        response = await client.get(robots_url)
        if response.status_code in {404, 410}:
            return True
        if response.status_code != 200:
            return False
        rules = RobotFileParser()
        rules.parse(response.text.splitlines())
        return rules.can_fetch(AGENT, url)
    except httpx.RequestError:
        return False


async def verify(client, result):
    url = result.get("url", "")
    try:
        if not await permitted(client, url):
            return None
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                return None
            if "text/html" in response.headers.get("content-type", ""):
                first = await anext(response.aiter_bytes(), b"")
                if b"paywall" in first[:12000].lower() and b"isaccessibleforfree" in first[:12000].lower():
                    return None
        return {"name": result.get("title") or url, "url": url, "text": result.get("content", "")[:1600], "tag": "web"}
    except (httpx.RequestError, ValueError):
        return None


class MockSearch:
    async def search(self, query):
        return []


class TavilySearch:
    async def search(self, query):
        key = os.getenv("TAVILY_API_KEY")
        if not key:
            return []
        with Session() as db:
            cached = get(db, "search", query)
        import time
        if cached and time.time() - cached["time"] < 3600:
            return cached["results"]
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=False, headers={"User-Agent": AGENT}) as client:
                response = await client.post("https://api.tavily.com/search",
                                             headers={"Authorization": f"Bearer {key}"},
                                             json={"query": query, "max_results": 8, "search_depth": "basic"})
                response.raise_for_status()
                checked = await asyncio.gather(*(verify(client, result) for result in response.json().get("results", [])))
        except (httpx.HTTPError, ValueError):
            return []
        valid = [result for result in checked if result]
        valid.sort(key=lambda result: not urlsplit(result["url"]).hostname.endswith(("ktu.edu.in", ".ac.in", ".edu.in")))
        with Session() as db:
            put(db, "search", query, {"results": valid, "time": time.time()})
            db.commit()
        return valid


search = MockSearch() if SEARCH_MOCK else TavilySearch()
