"""Small client for nemlig.com, the Danish online grocery store.

nemlig.com has no public, documented API. This uses the same web API the website does, with
your own customer login. It can change without notice; every call raises NemligError when a
response does not look as expected.
"""

from .client import Nemlig, NemligError, Product

__all__ = ["Nemlig", "NemligError", "Product"]
