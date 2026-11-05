"""mule init: scaffold a project dir with a starter config."""
import json
import os
import re

TEMPLATES = ("minimal", "python-lib", "web-app")

_GITIGNORE = """__pycache__/
*.pyc
.venv/
.mule/sessions/
"""

_README = """# %s

%s

## run

mule "your task here"
"""

_TEMPLATE_READMES = {
    "minimal": "a minimal mule project.",
    "python-lib": "a python library project, worked on with mule.",
    "web-app": "a small web app project, worked on with mule.",
}


def list_templates():
    # template names scaffold_project understands
    return list(TEMPLATES)

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

EXAMPLE_TEMPLATE = """you are doing a focused code review. be blunt and specific.

task: {{task}}

read the relevant files first, then report:
1. bugs you can see
2. the single most important fix
3. one style nit, at most
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
    templates_dir = os.path.join(target, ".mule", "templates")
    os.makedirs(templates_dir, exist_ok=True)

    write(os.path.join(target, "mule.json"),
          json.dumps(STARTER_CONFIG, indent=2) + "\n")
    write(os.path.join(target, "prompt.md"), PROMPT_TEMPLATE)
    write(os.path.join(commands_dir, "review.md"), EXAMPLE_COMMAND)
    write(os.path.join(templates_dir, "review.md"), EXAMPLE_TEMPLATE)
    return created


def scaffold_project(template, dest):
    # create a template skeleton in dest. refuses a non-empty dest.
    if template not in TEMPLATES:
        raise ValueError("unknown template: %s (try: %s)"
                         % (template, ", ".join(TEMPLATES)))
    dest = os.path.abspath(dest)
    if os.path.isdir(dest) and os.listdir(dest):
        raise FileExistsError("destination is not empty: %s" % dest)
    os.makedirs(dest, exist_ok=True)
    name = os.path.basename(dest) or "project"
    pkg = re.sub(r"[^0-9a-zA-Z_]", "_", name).strip("_") or "project"
    created = []

    def write(path, content):
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        created.append(path)

    write(os.path.join(dest, "mule.json"),
          json.dumps(STARTER_CONFIG, indent=2) + "\n")
    write(os.path.join(dest, "README.md"),
          _README % (name, _TEMPLATE_READMES[template]))
    write(os.path.join(dest, ".gitignore"), _GITIGNORE)
    write(os.path.join(dest, "prompt.md"), PROMPT_TEMPLATE)
    if template == "python-lib":
        write(os.path.join(dest, "pyproject.toml"),
              "[project]\nname = \"%s\"\nversion = \"0.1.0\"\n" % pkg)
        write(os.path.join(dest, "src", pkg, "__init__.py"), "")
        write(os.path.join(dest, "tests", "test_%s.py" % pkg),
              "\"\"\"tests for %s.\"\"\"\n" % pkg)
    if template == "web-app":
        write(os.path.join(dest, "index.html"),
              "<!doctype html>\n<html>\n<head><title>%s</title></head>\n"
              "<body>\n<h1>%s</h1>\n</body>\n</html>\n" % (name, name))
        write(os.path.join(dest, "app.py"),
              "# a tiny web app entry point\n")
    return created
