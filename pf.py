#!/usr/bin/env python3
"""pf - project find. Answers "where is X" in ~60 tokens instead of a file dump.

  pf <words>            best file: symbols, callers, what they render, purpose, then a digest
  pf -b <words>         facts only: the best file and its matching symbols
  pf -0 <words>         one line: path:line name
  pf -p <name> <words>  resolve a project by name, from anywhere
  pf <words> // <words> // -c    several questions in one call
  pf -f <file> [words]  that file in full, or its API
  pf -s path:line       the smallest meaningful slice (the enclosing declaration)
  pf -r <name>          who references this name, capped
  pf -c                 one line of counts: languages, views, routes, entry point
  pf -t                 project overview
  pf -i                 rebuild the index

A query that names a symbol which renders something answers in one line:
  path symbol:line holder:line > what it renders

Index lives at <root>/.pf/idx.tsv, tab separated, four tables: F file, S symbol,
U incoming usage, O outgoing usage. It rebuilds itself when the file count, the
newest mtime or the total size changes.
"""
import os, re, sys, subprocess, time, json, math

SKIP_DIR = {".git",".pf","node_modules","build",".build","dist","Pods","DerivedData",
            ".next","out","venv",".venv","__pycache__",".cache","coverage",".swiftpm",
            "xcuserdata",".gradle","target",".turbo",".vercel","worktrees",
            ".playwright-mcp","Carthage","vendor","third_party"}
EXT_LANG = {".swift":"swift",".ts":"ts",".tsx":"ts",".js":"js",".jsx":"js",".mjs":"js",
            ".py":"py",".html":"html",".css":"css",".scss":"css",".md":"md",
            ".rs":"rs",".go":"go",".rb":"rb",".java":"java",".kt":"kt",".sh":"sh",
            ".json":"json",".yml":"yml",".yaml":"yml",".toml":"toml",".sql":"sql"}
MAXBYTES = 1_200_000

DECL = [
 # (regex, kind)
 (re.compile(r'^\s*(?:public |private |internal |fileprivate |open |final |@MainActor )*'
             r'(struct|class|enum|protocol|extension|actor)\s+([A-Za-z_]\w*)'), "type"),
 (re.compile(r'^\s*(?:public |private |internal |fileprivate |static |override |@\w+ )*'
             r'func\s+([A-Za-z_]\w*)'), "func"),
 (re.compile(r'^\s*(?:@\w+(?:\([^)]*\))?\s+|public |private |internal |fileprivate |static |override |nonisolated )*func\s+([A-Za-z_]\w*)'), "func"),
 (re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$]\w*)'), "func"),
 (re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$]\w*)\s*=\s*(?:async\s*)?(?:\(|function|\w+\s*=>)'), "func"),
 (re.compile(r'^\s*(?:export\s+)?(?:interface|type|class|enum)\s+([A-Za-z_$]\w*)'), "type"),
 (re.compile(r'^\s*def\s+([A-Za-z_]\w*)'), "func"),
 (re.compile(r'^\s*class\s+([A-Za-z_]\w*)'), "type"),
 (re.compile(r'^\s*(?:@\w+(?:\([^)]*\))?\s+|public |private |internal |fileprivate |static |lazy |weak |open |final )*(?:let|var)\s+([A-Za-z_]\w*)\s*[:=]'), "var"),
 (re.compile(r'^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$]\w*)\s*[:=]'), "var"),
]
CALL = re.compile(r'\b([a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*){2,})\s*\(')
CSS_SEL = re.compile(r'^\s*([.#][A-Za-z_][\w-]*)[^{]*\{')
MD_HEAD = re.compile(r'^(#{1,3})\s+(.+)')
HTML_ID = re.compile(r'\bid="([A-Za-z_][\w-]*)"')
STOP = set("the a an and or of to in for with on at by is are be as it its this that from not "
           "if else return func var let const new void self true false null nil import export "
           "class struct enum type def print string int bool double float view body some any "
           "where case switch guard do try catch async await public private static override "
           "you we they them their what when how so but one two all can get set add".split())
WORD = re.compile(r"[A-Za-z][A-Za-z0-9']+")
CAMEL = re.compile(r'[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+')

def split_name(n):
    return [p.lower() for p in CAMEL.findall(n.replace('_',' ').replace('-',' ')) if p]

def split_stem(n):
    return [stem(p) for p in split_name(n)]

QSTOP = set("""a an the of to in for with on at by is are be as it its this that from not and or
find finds locate show tell during while after before user users its his her their there here
during across into onto out off over under about
decide decides decided choose chooses chosen determine determines handle handles
where which who what when how why does do did draw draws drawn show shows shown use uses used
call calls called place places placed get gets receive receives
property value file code line thing place part piece bit kind type sort way point item element
component module service object struct class func function method var let const variable field
main real actual exact single one two some any all each every own new old current live
project repo folder dir directory source sources app application system""".split())

SUF = (("ies","y"), ("ied","y"), ("ing",""), ("ed",""), ("es","e"), ("s",""))
def stem(w):
    """Both sides of every comparison pass through here, so consistency beats grammar."""
    for suf, rep in SUF:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)] + rep
    return w

def clean_query(words):
    """Strip filler so a plain English question scores like a keyword query."""
    q = [w.lower().strip(".,:;?!()[]'\"") for w in words if w]
    q = [w for w in q if w and len(w) > 1]
    keep = [w for w in q if w not in QSTOP and stem(w) not in QSTOP]
    return [stem(w) for w in (keep or q)]

def root_of(start):
    p = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(p,".git")) or os.path.isdir(os.path.join(p,".pf")): return p
        nxt = os.path.dirname(p)
        if nxt == p: return os.path.abspath(start)
        p = nxt

def under(root, p):
    """Resolve a path that came from the index. None when it leaves the project."""
    rr = os.path.realpath(root)
    full = os.path.realpath(os.path.join(rr, p))
    return full if full == rr or full.startswith(rr + os.sep) else None

def walk(root):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIR and not d.endswith(".xcodeproj")
                  and not d.endswith(".library") and not d.startswith(".pf")]
        for f in fns:
            ext = os.path.splitext(f)[1].lower()
            if ext in EXT_LANG:
                full = os.path.join(dp, f)
                if os.path.islink(full): continue   # a link to an .env outside the project stays out
                yield full, EXT_LANG[ext]

def comment_text(line, lang):
    s = line.strip()
    if lang in ("swift","ts","js","rs","go","java","kt"):
        if s.startswith(("//","/*","*")): return re.sub(r'^[/*\s]+','',s)
    if lang in ("py","sh","yml","toml"):
        if s.startswith("#"): return s.lstrip("# ")
    if lang == "html" and s.startswith("<!--"): return s.strip("<!->")
    return ""

def scan_state(root):
    """Count indexable files and the newest mtime. 0.2s on a 31k-file tree."""
    n, mx, tot = 0, 0.0, 0
    for path, _ in walk(root):
        n += 1
        try:
            st = os.stat(path)
            mx = max(mx, st.st_mtime); tot += st.st_size
        except OSError: pass
    return n, mx, tot

def build(root):
    t0 = time.time()
    frows, srows, vecs, uses, outs = [], [], [], {}, {}
    UBIQ = {"View","Text","String","Int","Bool","Double","Float","Color","Image","Spacer","Group",
            "VStack","HStack","ZStack","Array","Dictionary","Set","Date","URL","Data","Error",
            "Task","Promise","Record","Partial","Object","JSON","Math","Number","Boolean","Hono",
            "Binding","State","Published","Optional","Result","Void","Self","Any","AnyView"}
    USE = re.compile(r'\b([A-Z][A-Za-z0-9_]{2,})\s*[\(\{<.]')
    TYPEREF = re.compile(r'[:>]\s*([A-Z][A-Za-z0-9_]{2,})')
    for path, lang in walk(root):
        try:
            if os.path.getsize(path) > MAXBYTES: continue
            with open(path, "r", errors="ignore") as fh: lines = fh.read().split("\n")
        except OSError: continue
        rel = os.path.relpath(path, root)
        terms, names, purpose, nsym, cur_sym = {}, {}, "", 0, None
        base = os.path.splitext(os.path.basename(path))[0]
        for w in split_name(base): names[w] = names.get(w,0) + 6
        for i, ln in enumerate(lines, 1):
            if len(ln) > 400: ln = ln[:400]
            c = comment_text(ln, lang)
            if not c and lang not in ("md","json","yml","toml"):
                for nm in set(USE.findall(ln)) | set(TYPEREF.findall(ln)):
                    k = (nm, rel)
                    if k not in uses: uses[k] = i
                    if cur_sym and nm not in UBIQ and nm != cur_sym[0]:
                        o = outs.setdefault((rel, cur_sym[1]), [])
                        if len(o) < 8 and all(nm != x[0] for x in o): o.append((nm, i))
            if c:
                if not purpose and len(c) > 18: purpose = c[:110]
                for w in WORD.findall(c.lower()):
                    if w not in STOP and len(w) > 2: terms[w] = terms.get(w,0) + 1
                continue
            if lang == "md":
                m = MD_HEAD.match(ln)
                if m:
                    srows.append(("head", m.group(2).strip()[:60], rel, i, ""))
                    for w in WORD.findall(m.group(2).lower()):
                        if w not in STOP: terms[w] = terms.get(w,0)+3
                continue
            if lang == "css":
                m = CSS_SEL.match(ln)
                if m: srows.append(("css", m.group(1), rel, i, "")); nsym += 1
                continue
            if lang == "html":
                for hid in HTML_ID.findall(ln):
                    srows.append(("id", "#"+hid, rel, i, "")); nsym += 1
                continue
            if lang in ("ts","js","py","swift","rs","go"):
                mc = CALL.search(ln)
                if mc:
                    tail = mc.group(1).split(".")[-1]
                    if len(tail) > 3 and tail[0].islower():
                        srows.append(("call", tail, rel, i, ln.strip()[:100])); nsym += 1
            for rx, kind in DECL:
                m = rx.match(ln)
                if m:
                    name = m.group(2) if m.lastindex and m.lastindex > 1 else m.group(1)
                    if not name or name in ("init",): break
                    if kind == "var" and len(name) < 4: break
                    k = kind
                    if kind == "type" and lang == "swift" and re.search(r':\s*[^/]*\bView\b', ln): k = "view"
                    srows.append((k, name, rel, i, ln.strip()[:100]))
                    cur_sym = (name, i)
                    nsym += 1
                    for w in split_name(name): names[w] = names.get(w,0) + 4
                    break
        merged = dict(terms)
        for w, c in names.items(): merged[w] = merged.get(w, 0) + c
        vecs.append(merged)
        frows.append([rel, lang, len(lines), nsym, "", purpose])
    df = {}
    for v in vecs:
        for w in v: df[w] = df.get(w, 0) + 1
    N = max(1, len(vecs))
    for row, v in zip(frows, vecs):
        ranked = sorted(v.items(), key=lambda kv: -kv[1] * math.log(1 + N / (1 + df[kv[0]])))
        row[4] = " ".join(w for w, _ in ranked[:20])
    d = os.path.join(root,".pf")
    out = os.path.join(d,"idx.tsv")
    if os.path.islink(d) or os.path.islink(out):
        print("refusing: .pf is a symlink"); sys.exit(1)   # never write through a link
    os.makedirs(d, exist_ok=True)
    tmp = out + f".{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        n, mx, tot = scan_state(root)
        fh.write(f"#pf3\t{time.time():.0f}\t{n}\t{len(srows)}\t{mx:.3f}\t{tot}\n")
        for r in frows: fh.write("F\t" + "\t".join(TSV.sub(" ", str(x)) for x in r) + "\n")
        for r in srows: fh.write("S\t" + "\t".join(TSV.sub(" ", str(x)) for x in r) + "\n")
        bylen = {}
        for (nm, rel), i in uses.items(): bylen.setdefault(nm, []).append((rel, i))
        for nm, lst in bylen.items():
            if len(lst) > 10: lst = sorted(lst, key=lambda x: x[0].count("/"))[:10]
            for rel, i in lst: fh.write(f"U\t{nm}\t{rel}\t{i}\n")
        for (rel, sline), o in outs.items():
            if o: fh.write("O\t" + rel + "\t" + str(sline) + "\t" +
                           " ".join(f"{n}:{l}" for n, l in o) + "\n")
    os.replace(tmp, out)
    register(root)
    return out, len(frows), len(srows), time.time()-t0

TSV = re.compile(r"[\t\r\n]")               # a filename may hold a newline on macOS

CHAINED = False                                  # set while a // call runs: one answer per part
REG = os.path.expanduser("~/.cache/pf/roots.tsv")

def register(root):
    os.makedirs(os.path.dirname(REG), exist_ok=True)
    known = []
    if os.path.exists(REG):
        known = [l.rstrip("\n") for l in open(REG, errors="ignore") if l.strip()]
    if root not in known:
        known.append(root)
        open(REG, "w").write("\n".join(known) + "\n")

def resolve(name):
    """pf -p whisp <words>: find a registered root by name, no cd and no long path."""
    if not os.path.exists(REG): return None
    n = name.lower()
    hits = [r for r in (l.strip() for l in open(REG, errors="ignore")) if r and n in r.lower()]
    hits.sort(key=len)
    return hits[0] if hits else None

def load(root, auto=True):
    p = os.path.join(root,".pf","idx.tsv")
    if not os.path.exists(p):
        if not auto: return None, None, None
        build(root)
    elif auto:
        with open(p, errors="ignore") as fh: head = fh.readline().rstrip("\n").split("\t")
        if head[0] != "#pf3": build(root)
        else:
            n, mx, tot = scan_state(root)
            if n != int(head[2]) or mx > float(head[4]) + 0.001 or tot != int(head[5]):
                build(root)
    F, S, U, O = [], [], {}, {}
    with open(p, errors="ignore") as fh:
        for ln in fh:
            if ln.startswith("#"): continue
            parts = ln.rstrip("\n").split("\t")
            if parts[0] == "F" and len(parts) >= 7: F.append(parts[1:])
            elif parts[0] == "S" and len(parts) >= 5: S.append(parts[1:])
            elif parts[0] == "U" and len(parts) >= 4: U.setdefault(parts[1], []).append((parts[2], parts[3]))
            elif parts[0] == "O" and len(parts) >= 4: O[(parts[1], parts[2])] = parts[3]
    return F, S, U, O

def word_weights(S):
    df, n = {}, max(1, len(S))
    for r in S:
        for w in set(split_stem(r[1])): df[w] = df.get(w, 0) + 1
    top = math.log(1 + n)
    return {w: max(0.35, min(2.0, 2.0 * math.log(1 + n / (1 + c)) / top)) for w, c in df.items()}, n

VERB = {"next": ("advance", "forward", "step"), "move": ("advance", "shift"),
        "previous": ("back", "prior"), "delete": ("remove", "destroy"),
        "create": ("make", "add"), "show": ("render", "draw"), "start": ("begin", "open"),
        "stop": ("end", "close", "cancel"), "save": ("persist", "store"),
        "load": ("read", "fetch"), "check": ("verify", "validate")}

def matched_words(q, name, rel, ctx):
    parts = set(split_stem(name)); nl = stem(name.lower()); rl = rel.lower(); cl = ctx.lower()
    return {w for w in q if w in parts or w in nl or w in rl or w in cl}

def score_sym(q, kind, name, rel, ctx, W=None, q0=None, QF=None):
    parts = split_stem(name); s = 0.0
    nl = stem(name.lower())
    joined = "".join(parts)
    q0 = q0 or q
    for w in q:
        g = (W or {}).get(w, 1.0) * (QF or {}).get(w, 1.0)
        if nl == w or joined == "".join(q0): s += 30 * g
        elif w in parts: s += 14 * g
        elif nl.startswith(w) or joined.startswith(w): s += 8 * g
        elif w in nl: s += 5 * g
        elif w in rel.lower(): s += 2 * g
        elif w in ctx.lower(): s += 1 * g
    if len(q0) > 1 and all(any(w in p or p in w for p in parts) for w in q0): s += 18
    if set(parts) == set(q0): s += 15                     # exactly what was asked for
    elif len(parts) > 1 and set(parts) <= set(q): s += 20  # a compound name the query spells out
    s -= 2 * len(set(parts) - set(q))                     # every extra word is noise
    s += {"view":6,"type":5,"func":3,"css":2,"id":2,"var":1,"head":-8}.get(kind,0)
    return s

DOCWORD = {"doc","docs","spec","plan","note","notes","readme","playtest","brief","rule","rules"}
SIDE = ("/site/", "/site-dev/", "/marketing/", "/scripts/", "/tools/", "/tests/", "/test/",
        "/examples/", "/docs/", "/fixtures/", "/__tests__/", "/design/", "/research/")
SIDEWORD = {"web","site","marketing","test","tests","tool","tools","script","scripts",
            "doc","docs","design","research","example","examples","fixture","fixtures"}
LANGHINT = {"ios": "swift", "swift": "swift", "swiftui": "swift", "iphone": "swift",
            "app": None, "web": "js", "site": "js", "browser": "js", "page": "js",
            "backend": "ts", "server": "ts", "api": "ts", "route": "ts", "python": "py"}
def score_file(q, rel, terms, purpose, W=None, QF=None, want_lang=None, lang=None):
    s = 0.0; tl = [stem(t) for t in terms.split()]; low = rel.lower(); pl = " ".join(stem(x) for x in WORD.findall(purpose.lower()))
    for w in q:
        g = (W or {}).get(w, 1.0) * (QF or {}).get(w, 1.0)
        if w in tl: s += 8 * g
        if w in low: s += 6 * g
        if w in pl: s += 9 * g      # the file purpose is the strongest human signal
    if low.endswith(".md") and not (set(q) & DOCWORD): s *= 0.3
    hit = [w for w in set(q) if w in pl.split()]
    if len(hit) >= 2: s += 22 * sum((W or {}).get(w, 1.0) for w in hit)  # its own summary answers the question
    return s

def find(root, words, nf=3, fpat=None, answer=False, bare=False, brief=False):
    """Narrow to files, then show each file's matching symbols. One line per file."""
    q = clean_query(words)
    F, S, U, O = load(root)
    if fpat:
        fl = fpat.lower()
        S = [r for r in S if fl in r[2].lower()]
        F = [r for r in F if fl in r[0].lower()]
        if not q: q = [fl]
    W, _ = word_weights(S)
    want_lang = next((LANGHINT[w] for w in q if LANGHINT.get(w)), None)
    main_lang = None
    if not want_lang:                               # no hint: prefer the repo's dominant code language
        CODE = {"swift","ts","js","py","rs","go","rb","java","kt"}
        cnt = {}
        for r in F:
            if r[1] in CODE: cnt[r[1]] = cnt.get(r[1], 0) + 1
        if cnt:
            top_l, top_n = max(cnt.items(), key=lambda kv: kv[1])
            if top_n >= 3 * sum(v for k, v in cnt.items() if k != top_l) / max(1, len(cnt) - 1 or 1):
                main_lang = top_l
    if want_lang:                                  # a hint steers the ranking; it is not a symbol
        q = [w for w in q if not LANGHINT.get(w)] or q
    raw = [(score_sym(q, r[0], r[1], r[2], r[3] if len(r) > 3 else "", W), r) for r in S]
    pull, cover = {}, {}
    for sc, r in raw:
        if sc > 0:
            pull[r[2]] = pull.get(r[2], 0) + min(sc, 30) * (0.55 if sc >= 20 else 0.2)
            cover.setdefault(r[2], set()).update(
                matched_words(q, r[1], r[2], r[3] if len(r) > 3 else ""))
    wired_by = {}                                   # a matched symbol wired into the project
    for sc2, r2 in raw:
        if sc2 > 0 and O.get((r2[2], str(r2[3]))): wired_by[r2[2]] = wired_by.get(r2[2], 0) + 1
    fmeta = {r[0]: r for r in F}
    fscore = {}
    for r in F:
        cov = sorted(cover.get(r[0], ()), key=lambda w: -W.get(w, 1.0))
        bonus = 7 * sum(W.get(w, 1.0) for w in cov[1:])      # a file that answers several words at once
        sc = (score_file(q, r[0], r[4], r[5], W) + min(pull.get(r[0], 0), 40) + bonus
              + 12 * min(wired_by.get(r[0], 0), 3))
        if any(k in "/" + r[0].lower() for k in SIDE) and not (SIDEWORD & {w.lower() for w in words}):
            sc *= 0.35                              # the project's own source beats site and tooling
        if want_lang:                               # "the iOS app" means the Swift files
            sc *= 1.6 if r[1] == want_lang else 0.5
        elif main_lang and r[1] in ("swift","ts","js","py","rs","go","rb","java","kt"):
            sc *= 1.3 if r[1] == main_lang else 0.8
        if sc > 0: fscore[r[0]] = sc
    for path, pl in pull.items():
        if path not in fscore and pl > 10: fscore[path] = pl
    # a symbol in a file the query also points at wins over the same name elsewhere
    tot = [(sc + 0.5 * min(fscore.get(r[2], 0), 24), r) for sc, r in raw if sc > 0]
    tot.sort(key=lambda x: -x[0])
    chosen, order = {}, []
    def add(path, sc):
        m = fmeta.get(path)
        key = (os.path.basename(path), m[2] if m else "")
        if key in chosen:
            old = chosen[key]
            if old[0] == path:
                chosen[key] = (old[0], max(old[1], sc), old[2]); return
            chosen[key] = (old[0] if old[0].count("/") <= path.count("/") else path,
                           max(old[1], sc), old[2] + 1)
            return
        chosen[key] = (path, sc, 0); order.append(key)
    for sc, r in tot[:3]: add(r[2], sc)                     # never hide the best symbols
    for path, sc in sorted(fscore.items(), key=lambda kv: -kv[1]):
        if len(order) >= nf + 1: break
        add(path, sc)
    bypath = {}
    for sc, r in tot: bypath.setdefault(r[2], []).append((sc, r))
    # inside a file the query already points at, a verb's synonym counts too (next <-> advance)
    for path in {chosen[k][0] for k in order}:          # a file's namesake type is its identity
        base = os.path.splitext(os.path.basename(path))[0]
        have = bypath.setdefault(path, [])
        own = next((r for r in S if r[2] == path and r[1] == base and r[0] in ("type", "view")), None)
        if not own: continue
        peak = max((x[0] for x in have), default=24.0)
        lift = max(peak * 0.8, 12.0)
        at = next((i for i, x in enumerate(have) if x[1][1] == base), None)
        if at is None: have.append((lift, own))
        elif have[at][0] < lift: have[at] = (lift, have[at][1])
    alt = {stem(a) for w in q for a in VERB.get(w, ())}
    if alt:
        listed = {chosen[k][0] for k in order}
        for r in S:
            if r[2] not in listed or r[0] not in ("func", "view", "type"): continue
            if set(split_stem(r[1])) & alt:
                have = bypath.setdefault(r[2], [])
                peak = max((x[0] for x in have), default=24.0)
                lift = max(peak * 0.75, 12.0)
                at = next((i for i, x in enumerate(have) if x[1][1] == r[1] and x[1][3] == r[3]), None)
                if at is None: have.append((lift, r))
                elif have[at][0] < lift: have[at] = (lift, have[at][1])
    order.sort(key=lambda k: -chosen[k][1])
    lw = {w.lower() for w in words}
    wants_out = bool({"render","renders","draw","draws"} & lw)        # what it renders
    wants_in = bool({"consume","consumes","caller","callers","who"} & lw)  # who consumes it
    wants_edge = wants_out or wants_in
    edge_line = ""
    # one-line answer when the query names a symbol and that symbol renders something
    if order and not (brief or bare or fpat or answer):
        p0 = chosen[order[0]][0]
        for _, r0 in sorted(bypath.get(p0, []), key=lambda x: -x[0]):
            if stem(r0[1].lower()) not in set(q): continue
            o0 = O.get((p0, str(r0[3])))
            if not o0: continue
            hit0 = [x for x in o0.split() if set(q) & set(split_stem(x.split(":")[0]))]
            # fires whenever the query names a symbol that renders something — no wording luck needed
            rest0 = [x for x in o0.split() if x not in hit0]
            top = [f"{r0[1]}:{r0[3]}"]
            for _, r2 in sorted(bypath.get(p0, []), key=lambda x: -x[0]):
                tag = f"{r2[1]}:{r2[3]}"
                if tag not in top and r2[0] in ("type", "view", "func"):
                    top.append(tag); break
            print(f"{p0} " + " ".join(top) + " > " + " ".join((hit0 + rest0)[:3]))
            return
    lines, shown_out = [], False
    for key in order[:nf + 1]:
        path, sc, clones = chosen[key]
        m = fmeta.get(path) or [path, "", "?", "", "", ""]
        syms = bypath.get(path, [])
        if syms:
            cut = max(8, syms[0][0] * 0.5)
            syms = [x for x in syms if x[0] >= cut][:3]
        tail = " ".join(f"{r[1]}:{r[3]}" for _, r in syms)
        if fpat and not words:                      # "pf -f <file>" alone: the file's own API
            api = [r for r in S if r[2] == path and r[0] in ("func", "view", "type")]
            api.sort(key=lambda r: (-len(U.get(r[1], ())), r[3]))
            tail = " ".join(f"{r[1]}:{r[3]}" for r in api[:8]) or tail
        extra = f" +{clones}c" if clones else ""
        purpose = (m[5] or "")[:36]
        parts = [f"{path} {m[2]}L{extra}"]
        if tail: parts.append(tail)
        used, cand = [], []
        for _, r in syms[:2]:
            if r[0] not in ("view", "type", "func"): continue
            cand += [(fscore.get(up, 0), up, ui) for up, ui in U.get(r[1], []) if up != path]
        for _, up, ui in sorted(cand, key=lambda x: -x[0])[:2]:
            used.append(f"{os.path.basename(up)}:{ui}")
        if used: parts.append("used " + " ".join(used))
        wants_calls = bool({"call","calls","caller","callers","render","renders","consume",
                            "consumes","verify","verification","signature","invoke","invokes"}
                           & {w.lower() for w in words})
        if not lines:
            own = {r[1] for r in S if r[2] == path}
            proj = [(nm, ln) for nm, lst in U.items() for pth, ln in lst
                    if pth == path and nm not in own]
            calls = [r for r in S if r[2] == path and r[0] == "call"]
            calls += [("call", nm, path, ln, "") for nm, ln in proj
                      if any(r[1] == nm and r[0] in ("func", "type", "view") for r in S)]
            seen_c, pick = set(), []
            anchor = min((int(r[3]) for _, r in syms), key=lambda n: n) if syms else 0
            anchors = [int(r[3]) for _, r in syms] or [0]
            for r in sorted(calls, key=lambda r: min(abs(int(r[3]) - a) for a in anchors)):
                if r[1] in seen_c: continue
                seen_c.add(r[1]); pick.append(f"{r[1]}:{r[3]}")
                if len(pick) == 2: break
            if pick: parts.append("calls " + " ".join(pick))
        if not edge_line and syms and wants_edge:
            qs = set(q)
            def weight(r):                          # the rare query word names the thing, not "screen"
                hit = qs & set(split_stem(r[1]))
                return max((W.get(w, 1.0) for w in hit), default=0)
            for _, r in sorted((x for x in syms),
                               key=lambda x: (0 if stem(x[1][1].lower()) in qs else 1, -weight(x[1]))):
                if not (qs & set(split_stem(r[1]))): continue
                if wants_out:
                    o1 = O.get((path, str(r[3])))
                    if o1:
                        names = sorted(o1.split(),
                                       key=lambda x: (0 if qs & set(split_stem(x.split(":")[0])) else 1))
                        edge_line = f"{path} {r[1]}:{r[3]} > " + " ".join(names[:3]); break
                if wants_in:
                    u1 = [f"{up}:{ui}" for up, ui in U.get(r[1], []) if up != path][:3]
                    if u1:
                        edge_line = f"{path} {r[1]}:{r[3]} < " + " ".join(u1); break
        if not shown_out and syms:
            outs = []
            for _, r in syms[:3]:
                o = O.get((path, str(r[3])))
                if o:
                    nm = sorted(o.split(), key=lambda x: (0 if set(q) & set(split_stem(x.split(":")[0])) else 1))
                    outs.append(f"{r[1]}>" + ",".join(nm[:3]))
                if len(outs) == 2: break
            if outs: parts.append("uses " + " ".join(outs)); shown_out = True
        if purpose: parts.append(purpose)
        lines.append(" | ".join(parts))
    if not lines: print("none"); return
    if not (brief or bare or fpat or answer) and (CHAINED or len(q) <= 3):
        brief = True                                # a short "where is X" needs no prose
    if brief:                                       # just the facts: file and its matching symbols
        key = order[0] if order else None
        if key:
            path = chosen[key][0]
            syms = sorted(bypath.get(path, []), key=lambda x: -x[0])
            if syms:
                cut = max(8, syms[0][0] * 0.5)
                syms = [x for x in syms if x[0] >= cut][:4]
            who = []
            for _, r in syms[:2]:
                for up, ui in U.get(r[1], [])[:1]:
                    if up != path: who.append(f"{os.path.basename(up)}:{ui}")
            m2 = fmeta.get(path)
            why = "" if CHAINED else ((" | " + (m2[5] or "")[:36]) if m2 and m2[5] else "")
            used = "" if CHAINED else ((" | used " + " ".join(who[:2])) if who else "")
            print(path + " " + " ".join(f"{r[1]}:{r[3]}" for _, r in syms) + used + why)
        if CHAINED: return
        rest = []
        for key2 in order[1:3]:
            p2 = chosen[key2][0]
            s2 = sorted(bypath.get(p2, []), key=lambda x: -x[0])[:2]
            if s2: rest.append(p2 + ":" + ",".join(f"{r[1]}:{r[3]}" for _, r in s2))
        if rest: print("~ " + " ".join(rest))
        return
    if bare:                                        # pinpoint: the filename is the strongest signal
        qs = set(q)
        named = [p for p in fscore if qs & set(split_stem(os.path.splitext(os.path.basename(p))[0]))]
        for p2 in sorted(named, key=lambda p: -fscore[p])[:1]:
            if p2 not in {chosen[k][0] for k in order}: add(p2, fscore[p2] + 100)
        order = sorted(order, key=lambda k: (
            0 if qs & set(split_stem(os.path.splitext(os.path.basename(chosen[k][0]))[0])) else 1,
            -chosen[k][1]))
        for key in order[:1]:
            path, sc, clones = chosen[key]
            syms = sorted(bypath.get(path, []), key=lambda x: -x[0])[:1]
            if syms: print(f"{path}:{syms[0][1][3]} {syms[0][1][1]}")
            else: print(path)
        return
    if os.environ.get("PF_VERBOSE") or fpat:
        print("\n".join(lines)); return
    if wants_edge and edge_line:
        print(edge_line); return
    print(lines[0])
    rest = []
    for key in order[1:nf + 1]:
        path, sc, clones = chosen[key]
        syms = sorted(bypath.get(path, []), key=lambda x: -x[0])[:2]
        if syms:
            rest.append(path + ":" + ",".join(f"{r[1]}:{r[3]}" for _, r in syms[:2]))
        else: rest.append(path)
    if rest: print("~ " + " ".join(rest))
    if answer and tot:
        sc, r = tot[0]
        print("---")
        slice_at(root, f"{r[2]}:{r[3]}", span=14)

def counts(root):
    """One line of shape: languages, screens, entry, routes. For orientation in a single call."""
    F, S, U, O = load(root)
    langs = {}
    for r in F: langs[r[1]] = langs.get(r[1], 0) + 1
    views = len({r[1] for r in S if r[0] == "view"})
    ent = [r[0] for r in F if re.search(r"(^|/)(main|index|App|BecomingApp|server)\.(swift|ts|tsx|js|py)$", r[0])]
    ent.sort(key=lambda p: p.count("/"))
    routes = len({r[0] for r in F if "/routes/" in r[0] or "/api/" in r[0]})
    CODE = {"swift","ts","js","py","rs","go","rb","java","kt","html","css","sql","sh"}
    bits = [f"{k}:{v}" for k, v in sorted(((k, v) for k, v in langs.items() if k in CODE),
                                          key=lambda kv: -kv[1])[:2]]
    bits.append(f"views:{views}")
    if routes: bits.append(f"routes:{routes}")
    if ent: bits.append("entry:" + os.path.basename(ent[0]))
    print(" ".join(bits))

def overview(root, cap=30):
    F, S, U, O = load(root)
    langs, dirs = {}, {}
    for r in F:
        langs[r[1]] = langs.get(r[1], 0) + 1
        d = r[0].split("/")[0] if "/" in r[0] else "."
        dirs[d] = dirs.get(d, 0) + 1
    print("LANG " + " ".join(f"{k}:{v}" for k, v in sorted(langs.items(), key=lambda kv: -kv[1])[:4]))
    print("DIR " + " ".join(f"{k}:{v}" for k, v in sorted(dirs.items(), key=lambda kv: -kv[1])[:5]))
    nsym = {r[0]: int(r[3] or 0) for r in F}
    views = [r for r in S if r[0] == "view"]
    if views:
        views.sort(key=lambda r: -nsym.get(r[2], 0))
        seen, out = set(), []
        for r in views:
            if r[1] in seen: continue
            seen.add(r[1]); out.append(f"{r[1]}:{r[2].split('/')[-1]}")
        print(f"SCREENS({len(seen)}, biggest first) " + " ".join(out[:6]))
    ent = [r[0] for r in F if os.path.basename(r[0]).split(".")[0].lower() in
           ("index","main","app","server","__init__","cli","mod","root") or
           re.search(r"(App|Main|Server)\.(swift|ts|tsx|js|py)$", r[0])]
    ent.sort(key=lambda p: p.count("/"))
    if ent: print("ENTRY " + " ".join(ent[:3]))
    routes = sorted({os.path.dirname(r[0]) for r in F if "/routes/" in r[0] or "/api/" in r[0] or "/pages/" in r[0]})
    if routes:
        names = [os.path.basename(r[0]) for r in F if any(r[0].startswith(d + "/") for d in routes)]
        print(f"ROUTES({len(names)}) " + " ".join(sorted(set(names))[:12]))
    big = sorted(F, key=lambda r: -int(r[3] or 0))[:3]
    print("HUB " + " ".join(f"{r[0]}:{r[3]}s" for r in big))

def slice_at(root, spec, span=40):
    path, _, ln = spec.partition(":")
    full = under(root, path)
    if not full or not os.path.exists(full) or os.path.islink(full):
        print(f"outside the project: {path}"); return
    ln = int(ln or 1)
    with open(full, errors="ignore") as fh: lines = fh.read().split("\n")
    i = ln - 1
    # walk back to the declaration start, forward to brace balance
    start = i
    while start > 0 and lines[start].strip() and not re.match(r'^\s*(struct|class|func|def|enum|protocol|extension|var|let|const|export|@)', lines[start]):
        start -= 1
    depth, end = 0, i
    for j in range(start, min(len(lines), start + span*4)):
        depth += lines[j].count("{") - lines[j].count("}")
        end = j
        if j > start and depth <= 0 and "{" in "".join(lines[start:j+1]): break
    if end - start > span: end = start + span
    print(f"{path}:{start+1}-{end+1}")
    print("\n".join(lines[start:end+1]))

def refs(root, name, cap=12):
    """Who uses this name. Index file list keeps it to text files only."""
    F, _, _, _ = load(root)
    rank = {"swift":0,"ts":0,"tsx":0,"js":0,"py":0,"go":0,"rs":0,"rb":0,"java":0,"kt":0,
            "html":1,"css":1,"sql":1,"json":2,"yml":2,"toml":2,"sh":2,"md":3}
    hits, total = [], 0
    for r in F:
        full = under(root, r[0])
        if not full: continue
        try:
            with open(full, errors="ignore") as fh: body = fh.read()
        except OSError: continue
        if name not in body: continue
        for i, ln in enumerate(body.split("\n"), 1):
            if re.search(r"\b" + re.escape(name) + r"\b", ln):
                total += 1
                hits.append((rank.get(r[1], 2), r[0], i, ln.strip()[:88]))
    hits.sort()
    seen, out = set(), []
    for _, path, i, txt in hits:
        k = (os.path.basename(path), i, txt)
        if k in seen: continue
        seen.add(k); out.append(f"{path}:{i} {txt}")
        if len(out) >= cap: break
    print("\n".join(out) if out else "none")
    if total > cap: print(f"+{total-cap} more")

def main():
    a = sys.argv[1:]
    root = None
    if len(a) > 1 and a[0] == "-p":
        root = resolve(a[1])
        if not root: print(f"no project matching {a[1]!r}; run pf -i in it once"); return
        if os.environ.get("PF_VERBOSE"): print("@ " + root)   # paths are repo-relative by default
        a = a[2:]
    root = root or root_of(os.environ.get("PF_ROOT") or os.getcwd())
    if not a: print(__doc__); return
    if "//" in a:                                   # several questions, one call
        globals()["CHAINED"] = True
        groups, cur = [], []
        for x in a:
            if x == "//": groups.append(cur); cur = []
            else: cur.append(x)
        groups.append(cur)
        for i, g in enumerate(x for x in groups if x):
            if g[0] == "-t": overview(root)
            elif g[0] == "-c": counts(root)
            elif g[0] in ("-1", "-2", "-3"): find(root, g[1:], nf=int(g[0][1:]) - 1)
            elif g[0] == "-0": find(root, g[1:], bare=True)
            elif g[0] == "-b": find(root, g[1:], brief=True)
            elif g[0] == "-a": find(root, g[1:], answer=True)
            elif g[0] == "-f": find(root, g[2:], fpat=g[1])
            elif g[0] == "-s": slice_at(root, g[1])
            elif g[0] == "-r": refs(root, g[1])
            else: find(root, g)
        return
    if a[0] == "-i":
        p,nf,ns,dt = build(root); print(f"{p} {nf} files {ns} syms {dt:.1f}s"); return
    if a[0] == "-s": slice_at(root, a[1], int(a[2]) if len(a)>2 else 40); return
    if a[0] == "-r": refs(root, a[1]); return
    if a[0] == "-f": find(root, a[2:], fpat=a[1]); return
    if a[0] == "-t": overview(root); return
    if a[0] == "-c": counts(root); return
    if a[0] == "-a": find(root, a[1:], answer=True); return
    if a[0] in ("-1", "-2", "-3"): find(root, a[1:], nf=int(a[0][1:]) - 1); return
    if a[0] == "-0": find(root, a[1:], bare=True); return
    if a[0] == "-b": find(root, a[1:], brief=True); return
    find(root, a)

main()
