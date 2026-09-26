"""mule init: scaffold a project dir with a starter config."""
import json
import os

STARTER_CONFIG = {
    "model": "gpt-4o-mini",
    "base_url": "https://api.openai.com/v1",
    "max_steps": 25,
}

PROMPT_TEMPLATE = """you are a coding agent working inside a project directory.
read before you change, prefer small edits over rewrites, and keep
shell commands simple and non-interactive. when the task is done,
reply with a short summary and stop.
"""

EXAMPLE_COMMAND = """review the latest changes in the project: list the files
that changed, point out anything that looks like a bug, and suggest
one concrete improvement.
"""


def init_project(target=".", force=False):
    """write mule.json, prompt.md, and .mule/commands/. returns the
    list of files created. refuses to overwrite unless force."""
    target = os.path.abspath(target)
    created = []

    def write(path, content):
        if os.path.exists(path) and not force:
            raise FileExistsError("exists, use --force to overwrite: %s"
                                  % path)
        with open(path, "w") as f:
            f.write(content)
        created.append(path)

    os.makedirs(target, exist_ok=True)
    commands_dir = os.path.join(target, ".mule", "commands")
    os.makedirs(commands_dir, exist_ok=True)

    write(os.path.join(target, "mule.json"),
          json.dumps(STARTER_CONFIG, indent=2) + "\n")
    write(os.path.join(target, "prompt.md"), PROMPT_TEMPLATE)
    write(os.path.join(commands_dir, "review.md"), EXAMPLE_COMMAND)
    return created
