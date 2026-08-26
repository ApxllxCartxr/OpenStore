import yaml
from merchant.models import Product
from merchant.catalog.adapter import CatalogAdapter

class YAMLCatalogAdapter(CatalogAdapter):
    def __init__(self, config_path: str):
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        self._products: dict[str, Product] = {
            p["sku"]: Product(**p) for p in raw["products"]
        }

    def get_product(self, sku: str) -> Product | None:
        return self._products.get(sku)

    def search_products(self, query: str) -> list[Product]:
        q = query.lower()
        return [
            p for p in self._products.values()
            if q in p.name.lower() or q in p.description.lower()
        ]

    def list_all(self) -> list[Product]:
        return list(self._products.values())