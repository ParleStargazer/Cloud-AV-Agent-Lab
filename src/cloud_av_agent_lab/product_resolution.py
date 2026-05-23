from __future__ import annotations

from collections.abc import Iterable

from cloud_av_agent_lab.core.contracts import LabConfig, ProductProfile, VmProfile


class ProductResolutionError(ValueError):
    """Raised when a requested security product cannot be used safely."""


def normalize_product_id(value: str) -> str:
    return str(value or "").strip().casefold()


def resolve_security_product(
    config: LabConfig,
    vm: VmProfile,
    explicit_product_id: str = "",
    *,
    supported_products: Iterable[str] | None = None,
    purpose: str = "security product",
) -> ProductProfile:
    product_id = normalize_product_id(explicit_product_id) or normalize_product_id(
        vm.product_id
    )
    if not product_id:
        raise ProductResolutionError(f"{purpose} is not configured")

    vm_product_id = normalize_product_id(vm.product_id)
    if explicit_product_id and vm_product_id and product_id != vm_product_id:
        raise ProductResolutionError(
            "explicit --product does not match the selected VM profile product_id "
            f"({product_id!r} != {vm_product_id!r})"
        )

    products = {
        normalize_product_id(key): value for key, value in config.products.items()
    }
    product = products.get(product_id)
    if product is None:
        raise ProductResolutionError(f"unknown product id {product_id!r}")
    if not product.enabled:
        raise ProductResolutionError(f"product {product_id!r} is disabled in config")

    if supported_products is not None:
        supported = {normalize_product_id(item) for item in supported_products}
        if product_id not in supported:
            supported_text = ", ".join(sorted(supported)) or "none"
            raise ProductResolutionError(
                f"{purpose} is not supported for product {product_id!r}; "
                f"supported products: {supported_text}"
            )

    return product
