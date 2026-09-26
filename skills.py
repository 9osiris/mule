"""skills: .mule/skills/*/SKILL.md files appended to the system prompt."""
import os


def load_skills(skills_dir):
    # [(name, text)] sorted by name, skips missing dirs and empty files
    out = []
    if not os.path.isdir(skills_dir):
        return out
    for name in sorted(os.listdir(skills_dir)):
        path = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(path):
            continue
        with open(path, errors="replace") as f:
            text = f.read().strip()
        if text:
            out.append((name, text))
    return out


def skills_prompt(skills):
    # rendered block tacked onto the end of the system prompt
    if not skills:
        return ""
    parts = ["available skills (follow them when relevant):"]
    for name, text in skills:
        parts.append("## skill: %s\n%s" % (name, text))
    return "\n\n".join(parts)
