"""rough per-model pricing, dollars per 1m tokens. update as prices move."""

PRICING = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o4-mini": (1.10, 4.40),
    "o3-mini": (1.10, 4.40),
}


def cost_for(model, prompt_tokens, completion_tokens):
    # returns dollars, or None when the model has no pricing row
    row = PRICING.get(model)
    if row is None:
        return None
    pin, pout = row
    return ((prompt_tokens or 0) / 1e6 * pin
            + (completion_tokens or 0) / 1e6 * pout)


def fmt_cost(cost):
    if cost is None:
        return "unknown pricing"
    if cost < 0.0001:
        return "$%.6f" % cost
    return "$%.4f" % cost
