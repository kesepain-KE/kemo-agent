from pathlib import Path
import re, json, yaml

ROOT = Path(r'E:\code\kemo-agent\开发临时目录\obsidian')
OUT = ROOT / 'tmp_obsidian_audit.json'
LINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]')


def read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def parse_frontmatter(text: str):
    if not text.startswith('---\n'):
        return {}, text
    parts = text.split('---\n', 2)
    if len(parts) < 3:
        return {}, text
    fm_text = parts[1]
    body = parts[2]
    try:
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return fm, body

files = [p for p in ROOT.rglob('*.md') if '.bak' not in p.name]
items = []
name_to_paths = {}
for p in files:
    rel = p.relative_to(ROOT).as_posix()
    text = read_text(p)
    fm, body = parse_frontmatter(text)
    links = []
    for m in LINK_RE.finditer(text):
        target = m.group(1).strip()
        if target:
            links.append(target)
    name_to_paths.setdefault(p.stem, []).append(rel)
    items.append({
        'path': rel,
        'stem': p.stem,
        'has_frontmatter': bool(fm),
        'frontmatter_keys': sorted(fm.keys()),
        'tags': fm.get('tags'),
        'source': fm.get('source'),
        'module': fm.get('module'),
        'created': fm.get('created'),
        'round': fm.get('round'),
        'wikilinks': links,
        'wikilink_count': len(links),
        'lines': text.count('\n') + 1,
        'size': p.stat().st_size,
    })

# broken links by stem match only (lightweight)
all_stems = set(name_to_paths)
broken = {}
for it in items:
    bad = []
    for link in it['wikilinks']:
        t = link.split('|',1)[0]
        # ignore external / anchors / headings
        if t.startswith('http') or t.startswith('#'):
            continue
        base = t.split('#',1)[0].strip()
        if not base:
            continue
        # wikilink may be with folders or extension stripped
        stem = Path(base).stem
        if stem not in all_stems and base not in all_stems and base not in [x.replace('.md','') for x in all_stems]:
            bad.append(link)
    if bad:
        broken[it['path']] = bad

OUT.write_text(json.dumps({'count': len(items), 'items': items, 'broken': broken, 'name_to_paths': name_to_paths}, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(OUT)
