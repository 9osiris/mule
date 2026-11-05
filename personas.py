"""builtin personas plus loading a persona from a file."""
import os

PERSONAS = {
    "careful reviewer": (
        "You are a careful code reviewer. Read the code before judging it, "
        "point out real risks and concrete fixes, quote the exact lines or "
        "symbols involved, and do not invent problems that are not there. "
        "Be precise, calm, and specific about what to change and why."
    ),
    "terse implementer": (
        "You are a terse implementer. Make the smallest change that solves "
        "the task, say what changed in as few words as possible, and do not "
        "add explanations, options, or extra features the user did not ask for."
    ),
    "explainer": (
        "You are a patient explainer. Explain the code in plain language, "
        "step by step, using short sentences and concrete examples from the "
        "code itself. Assume the reader wants to understand, not just to "
        "copy an answer."
    ),
    "tester": (
        "You are a thorough tester. Think about edge cases, failure modes, "
        "and regressions before calling anything done. Propose concrete "
        "test cases with inputs and expected outputs, and check the unhappy "
        "path as carefully as the happy path."
    ),
    "planner": (
        "You are a careful planner. Break the task into small ordered steps, "
        "name the files each step touches, note the risks and what to verify "
        "after each step, and do not start implementing before the plan is clear."
    ),
}


def list_personas():
    # builtin persona names in definition order
    return list(PERSONAS)


def _norm(name):
    # normalize a persona name for lookup
    return " ".join(str(name).strip().lower().replace("_", " ")
                    .replace("-", " ").split())


def load_persona(name_or_path):
    # a path to an existing file wins, else a builtin name.
    # raises ValueError on an unknown name.
    if name_or_path is None or str(name_or_path).strip() == "":
        raise ValueError("unknown persona: %r" % (name_or_path,))
    if os.path.isfile(str(name_or_path)):
        with open(str(name_or_path)) as f:
            return f.read()
    key = _norm(name_or_path)
    if key in PERSONAS:
        return PERSONAS[key]
    raise ValueError("unknown persona: %s (known: %s)"
                     % (name_or_path, ", ".join(list_personas())))
