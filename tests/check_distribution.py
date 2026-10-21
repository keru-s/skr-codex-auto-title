"""Check self-contained skill resources, Markdown links and private-data exclusions."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/skr-codex-auto-title"
required = ["LICENSE", "SKILL.md", "agents/openai.yaml", "scripts/install.py", "assets/first_completion.py",
            "references/install.md", "references/manage.md", "references/verify.md"]
for name in required:
    assert (SKILL / name).is_file(), name
frontmatter = (SKILL / "SKILL.md").read_text().split("---", 2)[1]
assert "name: skr-codex-auto-title" in frontmatter and "description:" in frontmatter
assert "disable-model-invocation" not in frontmatter
for path in ROOT.rglob("*"):
    if not path.is_file() or any(part in (".git", "__pycache__", ".venv", ".DS_Store", "runtime", "validation-private") for part in path.relative_to(ROOT).parts):
        continue
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"/Users/" + r"[a-zA-Z0-9_-]+/", text), str(path)
    assert not re.search(r"sk-" + r"[A-Za-z0-9]{20,}", text), str(path)
    if path.suffix == ".py":
        compile(text, str(path), "exec")
    if path.suffix == ".md":
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "#")):
                continue
            assert (path.parent / target.split("#")[0]).exists(), (str(path), target)
print("Distribution resources, links, syntax and private-data checks passed")
