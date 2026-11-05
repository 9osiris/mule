"""token budgets, pricing rows, capability table, plan gate."""
from cost import PRICING
from cost import extend_pricing as _cost_extend_pricing


def approx_tokens(text):
    # local approximation only: about 1 token per 4 chars.
    # real tokenizers differ, so never use this for billing.
    if text is None:
        return 0
    size = len(text) if isinstance(text, str) else len(str(text))
    if size == 0:
        return 0
    return (size + 3) // 4


class TokenBudget:
    def __init__(self, max_tokens):
        # max_tokens is the cap, totals start at zero
        self.max_tokens = max_tokens
        self.prompt_tokens = 0
        self.completion_tokens = 0

    @property
    def total_tokens(self):
        # combined prompt plus completion tokens so far
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt_tokens, completion_tokens):
        # add one step of usage, None counts as zero
        self.prompt_tokens += prompt_tokens or 0
        self.completion_tokens += completion_tokens or 0
        return self.total_tokens

    def exceeded(self):
        # true only once usage passes the cap, not at it
        if self.max_tokens is None:
            return False
        return self.total_tokens > self.max_tokens

    def summary(self):
        # one human-readable line with totals and percent used
        total = self.total_tokens
        if self.max_tokens:
            pct = 100.0 * total / self.max_tokens
            return ("tokens: %d / %d used (%.0f%%)"
                    % (total, self.max_tokens, pct))
        return "tokens: %d used" % total


def extend_pricing(model, pin, pout):
    # add or replace a pricing row in cost.py's table at runtime
    return _cost_extend_pricing(model, pin, pout)


def plan_approval_gate(plan_text, approve_fn):
    # true only when approve_fn(plan_text) returns exactly True.
    # any other value, or an exception, means not approved.
    if approve_fn is None:
        return False
    try:
        return approve_fn(plan_text) is True
    except Exception:
        return False


# model name patterns to supported features. patterns are matched
# as substrings, longest pattern first, so specific models win.
CAPABILITIES = {
    "gpt-4o": {"vision": True, "json_schema": True, "streaming": True},
    "gpt-4.1": {"vision": True, "json_schema": True, "streaming": True},
    "gpt-4": {"vision": True, "json_schema": True, "streaming": True},
    "o4-mini": {"vision": False, "json_schema": True, "streaming": True},
    "o3-mini": {"vision": False, "json_schema": True, "streaming": True},
    "o3": {"vision": False, "json_schema": True, "streaming": True},
    "o4": {"vision": False, "json_schema": True, "streaming": True},
    "claude": {"vision": True, "json_schema": True, "streaming": True},
    "gemini": {"vision": True, "json_schema": True, "streaming": True},
    "llama": {"vision": False, "json_schema": True, "streaming": True},
    "deepseek": {"vision": False, "json_schema": True, "streaming": True},
    "qwen": {"vision": False, "json_schema": True, "streaming": True},
    "mistral": {"vision": False, "json_schema": True, "streaming": True},
}

_DEFAULT_CAPS = {"vision": True, "json_schema": True, "streaming": True}

# tools that need a capability: read_image needs vision
TOOL_FEATURES = {
    "read_image": "vision",
}


def capabilities_for(model):
    # capability row for a model, all-true default for unknowns
    if not model:
        return dict(_DEFAULT_CAPS)
    name = str(model).lower()
    for pattern in sorted(CAPABILITIES, key=len, reverse=True):
        if pattern in name:
            return dict(CAPABILITIES[pattern])
    return dict(_DEFAULT_CAPS)


def unsupported_tools(model, tool_names):
    # subset of tool_names the model cannot support, in order
    caps = capabilities_for(model)
    out = []
    for name in tool_names or []:
        feature = TOOL_FEATURES.get(name)
        if feature and not caps.get(feature, True):
            if name not in out:
                out.append(name)
    return out
