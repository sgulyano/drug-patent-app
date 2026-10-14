"""Polite HTTP client with rate limiting, retries and ASP.NET WebForms postback helpers."""
import logging
import time

import httpx
from selectolax.lexbor import LexborHTMLParser

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (patent-landscape research tool; contact: local analyst)"


class PoliteClient:
    def __init__(self, min_interval: float = 1.0, timeout: float = 120.0, retries: int = 4):
        # The DIP site has an incomplete certificate chain on some hosts; verify=False is a deliberate
        # concession for this read-only public data source.
        self.c = httpx.Client(timeout=timeout, follow_redirects=True, verify=False,
                              headers={"User-Agent": USER_AGENT})
        self.min_interval = min_interval
        self.retries = retries
        self._last = 0.0

    def _wait(self):
        dt = time.monotonic() - self._last
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._last = time.monotonic()

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(self.retries):
            self._wait()
            try:
                r = self.c.request(method, url, **kw)
                if r.status_code >= 500:
                    raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                return r
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                wait = 2 ** attempt * 2
                log.warning("%s %s failed (%s); retry in %ss", method, url, e, wait)
                time.sleep(wait)
        raise RuntimeError(f"giving up on {method} {url}")

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


def form_fields(html: str) -> dict:
    """Serialize a WebForms page's form the way a browser would (checked boxes/radios, selected options)."""
    t = LexborHTMLParser(html)
    d = {}
    for i in t.css("input"):
        n = i.attributes.get("name")
        typ = (i.attributes.get("type") or "text").lower()
        if not n or typ in ("submit", "button", "image"):
            continue
        if typ in ("radio", "checkbox"):
            if "checked" in i.attributes and "disabled" not in i.attributes:
                d[n] = i.attributes.get("value") or "on"
            continue
        d[n] = i.attributes.get("value") or ""
    for s in t.css("select"):
        n = s.attributes.get("name")
        o = s.css_first("option[selected]") or s.css_first("option")
        if n and o is not None:
            d[n] = o.attributes.get("value") or o.text()
    for ta in t.css("textarea"):
        n = ta.attributes.get("name")
        if n:
            d[n] = ta.text()
    return d


def postback(client: PoliteClient, url: str, html: str, target: str, **overrides) -> str:
    d = form_fields(html)
    d.update(overrides)
    d["__EVENTTARGET"] = target
    d["__EVENTARGUMENT"] = ""
    return client.post(url, data=d).text
