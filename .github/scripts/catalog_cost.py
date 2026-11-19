"""Estimate per-agent model and compute cost from saved sweep evidence.

Offline only. Prices are Azure retail list prices (USD, UK South, Global Standard)
and can be overridden with CATALOG_PRICE_<FIELD> environment variables. These are
estimates from measured usage, not an Azure bill; idle node time is not counted.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

# USD per 1M tokens: (input, cached input, output).
MODEL_PRICES = {
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5-mini": (0.25, 0.025, 2.00),
}
# USD per node hour, by node pool name (Linux pay-as-you-go list price).
NODE_POOL_RATES = {"nodepool1": 0.222, "d4sv6": 0.233, "h100": 8.725}
DEFAULT_NODE_RATE = 0.233

# Azure Retail Prices API meters (UK South, Global Standard) used to refresh the defaults above.
RETAIL_API = "https://prices.azure.com/api/retail/prices"
REGION = "uksouth"
MODEL_METERS = {
    "gpt-5.4": ("5.4 inp Gl 1M Tokens", "5.4 cd inp Gl 1M Tokens", "5.4 opt Gl 1M Tokens"),
    "gpt-5-mini": ("5 mini Inp Gl 1M Tokens", "5 mini cd Inp Gl 1M Tokens", "5 mini Opt Gl 1M Tokens"),
}
NODE_POOL_SKUS = {"nodepool1": "Standard_D4s_v4", "d4sv6": "Standard_D4s_v6", "h100": "Standard_NC40ads_H100_v5"}
PRICE_SOURCES: dict[str, str] = {}


def _retail(filter_text: str) -> list[dict]:
    import urllib.parse
    import urllib.request
    url = f"{RETAIL_API}?$filter={urllib.parse.quote(filter_text)}"
    items: list[dict] = []
    while url and len(items) < 500:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "catalog-cost"}), timeout=30) as r:
            page = json.load(r)
        items += page.get("Items") or []
        url = page.get("NextPageLink")
    return items


def refresh_prices(retail=_retail) -> dict[str, str]:
    """Replace built-in prices with current Azure list prices where a meter exists.

    Any price the API does not return keeps its built-in default, and the source of
    every price is recorded so the report never presents a guess as a list price.
    """
    for model, meters in MODEL_METERS.items():
        PRICE_SOURCES[f"model:{model}"] = "built-in default"
        try:
            rows = {}
            for meter in meters:
                for i in retail(f"armRegionName eq '{REGION}' and meterName eq '{meter}' and priceType eq 'Consumption'"):
                    rows[i["meterName"]] = i["unitPrice"]
        except Exception:  # Network or API failure: keep defaults.
            continue
        if all(m in rows for m in meters):
            MODEL_PRICES[model] = tuple(float(rows[m]) for m in meters)  # type: ignore[assignment]
            PRICE_SOURCES[f"model:{model}"] = "Azure Retail Prices API"
    for pool, sku in NODE_POOL_SKUS.items():
        PRICE_SOURCES[f"node:{pool}"] = "built-in default"
        try:
            rows = [i for i in retail(f"armRegionName eq '{REGION}' and armSkuName eq '{sku}' and priceType eq 'Consumption'")
                    if i.get("serviceName") == "Virtual Machines" and "Windows" not in i.get("productName", "")
                    and not re.search(r"Spot|Low Priority", i.get("meterName", ""))]
        except Exception:
            continue
        if rows:
            NODE_POOL_RATES[pool] = float(rows[0]["unitPrice"])
            PRICE_SOURCES[f"node:{pool}"] = "Azure Retail Prices API"
    return dict(PRICE_SOURCES)


def _model_key(model: str | None) -> str | None:
    text = (model or "").lower().replace("_", "-")
    if "mini" in text and "5" in text:
        return "gpt-5-mini"
    if re.search(r"5[.-]4", text):
        return "gpt-5.4"
    return None


def model_prices(model: str | None) -> tuple[float, float, float] | None:
    base = MODEL_PRICES.get(_model_key(model) or "")
    over = [os.getenv(f"CATALOG_PRICE_{k}") for k in ("INPUT", "CACHED", "OUTPUT")]
    if all(over):
        return tuple(float(v) for v in over)  # type: ignore[return-value]
    return base


def node_rates() -> dict[str, float]:
    raw = os.getenv("CATALOG_NODE_RATES", "")
    rates = dict(NODE_POOL_RATES)
    for pair in raw.split(","):
        if "=" in pair:
            name, value = pair.split("=", 1)
            rates[name.strip()] = float(value)
    return rates


def _parse(value: str) -> datetime:
    # Azure returns 7 fractional digits; datetime accepts at most 6.
    text = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    return datetime.fromisoformat(text)


def _seconds(start: str | None, end: str | None) -> float:
    try:
        return max((_parse(end) - _parse(start)).total_seconds(), 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _evidence_dir(evidence_root: Path | None, agent: str, response_id: str | None) -> Path | None:
    if not evidence_root:
        return None
    for path in evidence_root.glob(f"*/live/{agent}/response.json"):
        try:
            if not response_id or json.loads(path.read_text(encoding="utf-8")).get("id") == response_id:
                return path.parent
        except (OSError, ValueError):
            continue
    return None


def agent_cost(row: dict, evidence_root: Path | None) -> dict:
    """Token counts, tool run time per node pool and estimated dollars for one ledger row."""
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    tin = int(usage.get("input_tokens") or 0)
    cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
    tout = int(usage.get("output_tokens") or 0)
    model, pools = row.get("model"), {}
    folder = _evidence_dir(evidence_root, row.get("catalog_agent", ""), row.get("response_id"))
    if folder:
        try:
            response = json.loads((folder / "response.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            response = {}
        model = model or response.get("model")
        # Only the pool named in a tool call's own arguments, never the GetNodePoolContext listing.
        call_pools = [m.group(1) for item in response.get("output") or []
                      if isinstance(item, dict) and item.get("type") == "function_call"
                      for m in [re.search(r"node[Pp]ools/([A-Za-z0-9-]+)", str(item.get("arguments") or ""))] if m]
        try:
            operations = json.loads((folder / "operations.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            operations = {}
        for index, operation in enumerate(v for v in operations.values() if isinstance(v, dict)):
            result = operation.get("result") or {}
            pool = call_pools[min(index, len(call_pools) - 1)] if call_pools else "unknown"
            pools[pool] = pools.get(pool, 0.0) + _seconds(result.get("createdAt"), result.get("completedAt"))
    prices = model_prices(model)
    rates = node_rates()
    model_usd = None
    if prices and usage:
        model_usd = round(max(tin - cached, 0) / 1e6 * prices[0] + cached / 1e6 * prices[1]
                          + tout / 1e6 * prices[2], 4)
    compute_usd = round(sum(sec / 3600 * rates.get(p, DEFAULT_NODE_RATE) for p, sec in pools.items()), 4)
    return {"model": model, "input_tokens": tin if usage else None, "cached_tokens": cached if usage else None,
            "output_tokens": tout if usage else None, "tool_seconds": round(sum(pools.values()), 1),
            "node_pools": sorted(pools), "model_cost_usd": model_usd, "compute_cost_usd": compute_usd,
            "total_cost_usd": None if model_usd is None else round(model_usd + compute_usd, 4)}


def cost_totals(agents: list[dict]) -> dict:
    def total(key):
        return round(sum(a.get(key) or 0 for a in agents), 4)
    known = [a for a in agents if a.get("model_cost_usd") is not None]
    return {"model_cost_usd": total("model_cost_usd"), "compute_cost_usd": total("compute_cost_usd"),
            "total_cost_usd": total("total_cost_usd"), "tool_seconds": total("tool_seconds"),
            "cost_known_agents": len(known)}


def price_note() -> str:
    sources = sorted(set(PRICE_SOURCES.values())) or ["built-in default"]
    return ("Cost is an estimate from measured usage at Azure list prices (USD, UK South; source: "
            + ", ".join(sources) + "): "
            + "; ".join(f"{m} ${p[0]}/${p[1]}/${p[2]} per 1M input/cached/output tokens" for m, p in MODEL_PRICES.items())
            + "; node pools " + ", ".join(f"{k} ${v}/h" for k, v in NODE_POOL_RATES.items())
            + ". Tool time is operation start to finish (includes queueing); idle nodes are not counted. Not an Azure bill.")
