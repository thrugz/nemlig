from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

BASE = "https://www.nemlig.com"
SEARCH = "https://webapi.prod.knl.nemlig.it/searchgateway/api/search"

# The website sends these. Do not add Content-Type to GET requests: the search gateway then
# tries to read the empty body as JSON and fails.
HEADERS = {"Device-Size": "desktop", "Platform": "web", "Version": "11.201.0", "Accept": "application/json"}

TOKEN_TTL = 280  # seconds; the bearer token appears to expire after about 300
PARAMS_TTL = 1800  # search parameters (timestamp, timeslot, delivery zone)


class NemligError(RuntimeError):
    pass


@dataclass
class Product:
    id: int
    name: str
    price: float | None
    unit_price: str
    labels: list[str] = field(default_factory=list)

    @property
    def organic(self) -> bool:
        return any("kologisk" in label.lower() for label in self.labels) or "øko" in self.name.lower()


class Nemlig:
    """A nemlig.com customer. Pass the email and password you log in with."""

    def __init__(self, username: str, password: str, timeout: float = 30.0):
        self._login_data = {"Username": username, "Password": password}
        self._http = httpx.Client(base_url=BASE, timeout=timeout, follow_redirects=True)
        self._bearer = ""
        self._xsrf = ""
        self._auth_at = 0.0
        self._params: dict[str, str] = {}
        self._params_at = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Nemlig:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- login ----

    def _login(self) -> None:
        """Three steps: an anti-forgery token, a bearer token, then the login itself."""
        r = self._http.get("/webapi/AntiForgery", headers=HEADERS)
        _check(r, "anti-forgery token")
        # The token comes in the body ({"Header": "X-XSRF-TOKEN", "Value": ...}); older
        # versions sent it as a plain string or only as a cookie.
        body = _json(r)
        if isinstance(body, dict):
            xsrf = body.get("Value") or body.get("antiForgeryToken") or ""
        else:
            xsrf = body or ""
        xsrf = xsrf or self._http.cookies.get("XSRF-COOKIE-TOKEN") or r.headers.get("X-XSRF-TOKEN", "")
        if not xsrf:
            raise NemligError("login: no anti-forgery token")

        r = self._http.get("/webapi/Token", headers={**HEADERS, "X-XSRF-TOKEN": xsrf})
        _check(r, "bearer token")
        body = _json(r)
        bearer = body if isinstance(body, str) else (body or {}).get("access_token") or (body or {}).get("token", "")
        if not bearer:
            raise NemligError("login: no bearer token")

        r = self._http.post(
            "/webapi/login",
            headers={**HEADERS, "X-XSRF-TOKEN": xsrf, "Authorization": f"Bearer {bearer}"},
            json=self._login_data,
        )
        _check(r, "login")
        body = _json(r)
        if isinstance(body, dict) and body.get("IsLoggedIn") is False:
            raise NemligError(f"login refused: {body.get('ErrorMessage', 'unknown reason')}")
        self._bearer, self._xsrf, self._auth_at = bearer, xsrf, time.time()

    def _headers(self) -> dict[str, str]:
        if not self._bearer or time.time() - self._auth_at > TOKEN_TTL:
            self._login()
        return {
            **HEADERS,
            "Authorization": f"Bearer {self._bearer}",
            "X-XSRF-TOKEN": self._xsrf,
            "X-Correlation-Id": str(uuid.uuid4()),
        }

    # ---- search ----

    def _search_params(self) -> dict[str, str]:
        """Search needs the catalogue timestamp, your delivery timeslot and delivery zone."""
        if self._params and time.time() - self._params_at < PARAMS_TTL:
            return self._params
        h = self._headers()
        r = self._http.get("/webapi/v2/AppSettings/Website", headers=h)
        _check(r, "app settings")
        timestamp = str((_json(r) or {}).get("CombinedProductsAndSitecoreTimestamp", ""))
        r = self._http.get("/?GetAsJson=1", headers=h)
        _check(r, "page settings")
        page = _json(r) or {}
        settings = page.get("Settings", page)
        params = {
            "timestamp": timestamp,
            "timeslotUtc": str(settings.get("TimeslotUtc", "")),
            "deliveryZoneId": str(settings.get("DeliveryZoneId", "")),
        }
        if not all(params.values()):
            raise NemligError("search parameters missing from the page settings")
        self._params, self._params_at = params, time.time()
        return params

    def search(self, query: str, limit: int = 10) -> list[Product]:
        r = self._http.get(
            SEARCH, params={"query": query, "take": str(limit), **self._search_params()}, headers=self._headers()
        )
        _check(r, f"search {query!r}")
        data = _json(r) or {}
        raw = data.get("Products", data.get("products", []))
        items = raw.get("Products", []) if isinstance(raw, dict) else raw
        return [_product(p) for p in items if p.get("Id") or p.get("ProductId")]

    # ---- basket ----

    def basket(self) -> dict[str, Any]:
        """The basket as nemlig returns it. `Lines` holds the items, `Total` the sum."""
        r = self._http.get("/webapi/basket/GetBasket", headers=self._headers())
        _check(r, "basket")
        return _json(r) or {}

    def basket_lines(self) -> list[dict[str, Any]]:
        b = self.basket()
        return b.get("Lines", b.get("lines", []))

    def set_quantity(self, product_id: int, quantity: int) -> None:
        """Set how many of a product the basket holds. 0 removes it."""
        r = self._http.post(
            "/webapi/basket/AddToBasket",
            headers=self._headers(),
            json={"ProductId": int(product_id), "Quantity": int(quantity)},
        )
        _check(r, f"set quantity of {product_id}")

    def add(self, product_id: int, quantity: int = 1) -> None:
        """Add to what the basket already holds."""
        have = next((_qty(line) for line in self.basket_lines() if _pid(line) == int(product_id)), 0)
        self.set_quantity(product_id, have + quantity)

    def remove(self, product_id: int) -> None:
        self.set_quantity(product_id, 0)

    def clear(self) -> int:
        """Empty the basket. Returns how many lines were removed."""
        lines = self.basket_lines()
        for line in lines:
            self.remove(_pid(line))
        return len(lines)


def _check(r: httpx.Response, what: str) -> None:
    if r.status_code >= 400:
        raise NemligError(f"{what}: HTTP {r.status_code}")


def _json(r: httpx.Response) -> Any:
    try:
        return r.json() if r.text else None
    except ValueError as e:
        raise NemligError(f"{r.request.url.path}: not JSON") from e


def _pid(line: dict) -> int:
    return int(line.get("ProductId") or line.get("productId") or line.get("Id"))


def _qty(line: dict) -> int:
    return int(line.get("Quantity") or line.get("quantity") or 0)


def _product(p: dict) -> Product:
    labels = []
    for label in p.get("Labels") or []:
        name = label.get("Name") if isinstance(label, dict) else label
        if name:
            labels.append(str(name))
    price = p.get("Price")
    return Product(
        id=int(p.get("Id") or p.get("ProductId")),
        name=str(p.get("Name") or ""),
        price=float(price) if isinstance(price, (int, float)) else None,
        unit_price=str(p.get("UnitPrice") or ""),
        labels=labels,
    )
