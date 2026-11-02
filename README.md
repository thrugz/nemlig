# nemlig

A small Python client for [nemlig.com](https://www.nemlig.com), the Danish online grocery store: search products and manage your basket.

nemlig.com has no public, documented API. This uses the same web API the website does, with your own customer login. Nothing here places an order: checkout stays with you, on the website or in the app.

## What it can do

| Task | How |
|---|---|
| Search products (name, price, unit price, labels, organic) | `search(query, limit)` |
| Read the basket | `basket()`, `basket_lines()` |
| Add to the basket | `add(product_id, quantity)` |
| Set an exact quantity, 0 removes | `set_quantity(product_id, quantity)` |
| Remove one product | `remove(product_id)` |
| Empty the basket | `clear()` |

## Install

```sh
pip install git+https://github.com/thrugz/nemlig.git
```

Python 3.10 or newer. The only dependency is `httpx`.

## Use

```python
import os
from nemlig import Nemlig

with Nemlig(os.environ["NEMLIG_USERNAME"], os.environ["NEMLIG_PASSWORD"]) as n:
    for want in ["mælk", "rugbrød", "æbler"]:
        hits = n.search(want)
        pick = next((p for p in hits if p.organic), hits[0] if hits else None)
        if pick:
            n.add(pick.id)
            print(f"{pick.name}: {pick.price} kr")
```

Keep the login in environment variables or a secret store, never in code.

## How nemlig.com works, as far as we found

- **Login takes three calls.** `GET /webapi/AntiForgery` returns `{"Header": "X-XSRF-TOKEN", "Value": "…"}`. `GET /webapi/Token` with that header returns a bearer token. `POST /webapi/login` with `{"Username", "Password"}` and both headers logs you in. A wrong login answers HTTP 400 with `ErrorCode` 4.
- **The bearer token lasts about five minutes.** The client logs in again after 280 seconds.
- **Search lives on another host,** `webapi.prod.knl.nemlig.it/searchgateway/api/search`, as a GET with `query`, `take`, and three values the site reads from its own settings: `timestamp` (from `/webapi/v2/AppSettings/Website`), `timeslotUtc` and `deliveryZoneId` (from `/?GetAsJson=1`). Results and prices depend on your delivery zone.
- **Do not send `Content-Type` on GET requests.** The search gateway then tries to parse the empty body as JSON and fails.
- **The basket has one write call.** `POST /webapi/basket/AddToBasket` with `{"ProductId", "Quantity"}` sets the quantity of that product; 0 removes it.

The site sends `Device-Size`, `Platform` and `Version` headers; the client does too. If nemlig.com changes its API, calls raise `NemligError` rather than returning something half right.

## Credits

The login and search flow were first worked out in [eisbaw/nemlig_cli](https://github.com/eisbaw/nemlig_cli).

## Good manners

This uses your own account to do what you could do by hand on the website. Keep the volume at what a person would do.

Not affiliated with nemlig.com.

## License

MIT
