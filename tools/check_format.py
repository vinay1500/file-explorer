"""Catch, without installing anything, the formatting mistakes that keep costing CI runs.

This project has no local runtime (see CONTRIBUTING): nothing installs on the
development machine, and CI is where every command is proven. That is a
deliberate trade, and its cost is that a formatting slip is a ninety-second
round trip instead of a two-second one. Roughly a quarter of all CI runs on
this project went that way before this existed.

**This is not a linter and not a ruff replacement.** It implements six rules,
chosen because they are the ones that actually recurred:

  * a string split across lines that would fit on one (whole concatenation
    runs, not pairs -- ruff only joins when the *entire* run fits)
  * a wrapped call or signature that would fit on one line, respecting the
    magic trailing comma
  * a line over the 100-column limit
  * an import that is never used, counted against code with docstrings and
    comments blanked, and honouring `# noqa`
  * imported names, and adjacent `from X import` statements, out of isort order
  * the wrong number of blank lines after a definition or an import block

It under-reports by design. Everything it flags, ruff would also flag; the
reverse is not true, and CI remains the authority.

A seventh rule -- magic trailing commas, which ruff explodes one element per
line -- was written and removed. It produced fourteen false positives against
a tree CI had already verified clean, because nested structures legitimately
put several tokens on one line. **A checker that disagrees with the linter it
stands in for is worse than no checker**, so the rule is recorded in the
contributor notes as something to watch rather than something automated.

Standard library only. Run it from `apps/api`:

    python3 ../../tools/check_format.py .

Validated by running it against a commit CI had already passed: a false
positive here is a bug in this file, not in the code it is reading.
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

LIMIT = 100
SKIP = (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT)
FSTRING_START = getattr(tokenize, "FSTRING_START", None)
FSTRING_END = getattr(tokenize, "FSTRING_END", None)
FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", -1)


class Unit:
    """One string literal, however the tokenizer spelled it."""

    def __init__(self, line: int, text: str) -> None:
        self.line = line
        self.text = text

    @property
    def block(self) -> bool:
        return self.text.lstrip("fFrRbB").startswith(('"""', "'''"))


def units(src: str) -> list[Unit | None]:
    """String literals in order; None marks any other meaningful token."""
    toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type not in SKIP]
    out: list[Unit | None] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.type == tokenize.STRING:
            out.append(Unit(t.start[0], t.string))
        elif FSTRING_START is not None and t.type == FSTRING_START:
            start, depth, text = t, 1, t.string
            i += 1
            while i < len(toks) and depth:
                text += toks[i].string
                if toks[i].type == FSTRING_START:
                    depth += 1
                elif toks[i].type == FSTRING_END:
                    depth -= 1
                i += 1
            out.append(Unit(start.start[0], text))
            continue
        else:
            out.append(None)
        i += 1
    return out


def wrapped_but_fits(src: str, lines: list[str]) -> list[tuple[int, int]]:
    """Bracketed constructs split over lines that would fit on one.

    ruff format collapses these -- UNLESS there is a magic trailing comma
    before the closing bracket, which pins them open. That exception is the
    whole reason this is token-based rather than a regex.
    """
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline)
                if t.type not in (tokenize.COMMENT,)]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []

    out, stack = [], []
    for i, t in enumerate(toks):
        if t.type == tokenize.OP and t.string in "([{":
            stack.append((t, i))
        elif t.type == tokenize.OP and t.string in ")]}":
            if not stack:
                continue
            op, oi = stack.pop()
            if op.start[0] == t.start[0]:
                continue                      # already on one line
            if any(l.lstrip().startswith("#") for l in lines[op.start[0]:t.start[0] - 1]):
                continue                      # a comment inside pins it open
            prev = [x for x in toks[oi + 1:i] if x.type not in (tokenize.NL, tokenize.NEWLINE,
                                                                tokenize.INDENT, tokenize.DEDENT)]
            if prev and prev[-1].type == tokenize.OP and prev[-1].string == ",":
                continue                      # magic trailing comma: ruff keeps it exploded
            inner = " ".join(l.strip() for l in lines[op.start[0] - 1:t.start[0]])
            inner = re.sub(r"\s+", " ", inner)
            inner = re.sub(r"([(\[{]) ", r"\1", inner)
            inner = re.sub(r" ([)\]},])", r"\1", inner)
            indent = len(lines[op.start[0] - 1]) - len(lines[op.start[0] - 1].lstrip())
            if indent + len(inner.strip()) <= LIMIT:
                out.append((op.start[0], indent + len(inner.strip())))
                continue

            # ruff has a second move this missed for a day: when the whole
            # construct does not fit, it still joins the bracket's *contents*
            # onto one line and leaves the brackets split. Narrowly detected --
            # only when the contents occupy exactly two lines, because that is
            # the case seen in practice and a wider rule would start guessing.
            body = lines[op.start[0]:t.start[0] - 1]
            if len(body) == 2 and all(l.strip() for l in body):
                joined = re.sub(r"\s+", " ", " ".join(l.strip() for l in body))
                body_indent = len(body[0]) - len(body[0].lstrip())
                if body_indent + len(joined) <= LIMIT:
                    out.append((op.start[0] + 1, body_indent + len(joined)))
    return out


def code_only(src: str) -> str:
    """`src` with docstrings and comments blanked out, line positions kept.

    Only those two. Blanking every string literal looks tidier and is wrong:
    `cast("CursorResult[Any]", result)` is a real use of both names, and ruff
    counts it. A checker that disagrees with the linter it stands in for is
    worse than no checker.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src

    blank: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            blank.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))

    out = []
    for n, line in enumerate(src.splitlines(keepends=True), 1):
        if n in blank:
            out.append("\n" if line.endswith("\n") else "")
        else:
            out.append(re.sub(r"#.*", "", line))
    return "".join(out)


def unused_imports(src: str, path: Path) -> list[str]:
    """Names imported and never mentioned again — ruff F401.

    Deliberately crude: a name counts as used if it appears anywhere else in the
    file as a whole word, including inside a string annotation or a docstring.
    That under-reports, which is the right direction for a pre-push check.

    `__init__.py` is skipped: an import there is usually a re-export.
    """
    if path.name == "__init__.py":
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    lines = src.splitlines()
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        # An import kept deliberately for its side effect carries a noqa. ruff
        # honours it, so this must too -- a checker that disagrees with the
        # linter it stands in for is worse than no checker.
        line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
        if "# noqa" in line:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            bound = alias.asname or alias.name.split(".")[0]
            # Counted against code only. A name that survives solely because a
            # docstring mentions it is still unused as far as ruff cares, and
            # that is exactly how DBAPIError slipped past this check once.
            if len(re.findall(rf"\b{re.escape(bound)}\b", code_only(src))) <= 1:
                out.append(f"{path}:{node.lineno}: {bound!r} is imported and never used (F401)")
    return out


def unsorted_import_names(src: str, path: Path) -> list[str]:
    """Names inside a `from x import (...)` block that are not in isort order.

    ruff's I001 rewrites these, so getting one wrong costs a CI cycle for a
    reordering. isort sorts case-sensitively, which puts every capitalised name
    before every lowercase one -- `force_sort_within_sections` is not in play
    here, so a plain sorted() on the raw names matches.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or len(node.names) < 2:
            continue
        names = [a.asname or a.name for a in node.names]
        if names != sorted(names):
            first = next((b for a, b in zip(names, names[1:]) if b < a), names[-1])
            out.append(
                f"{path}:{node.lineno}: imported names are out of isort order "
                f"({first!r} should come earlier)"
            )
    return out


def blank_lines_after_top_level(src: str, path: Path) -> list[str]:
    """Top-level defs and classes need exactly two blank lines after them.

    ruff format inserts them, so one missing costs a cycle. Only checks the
    gap *after* a definition; the gap before is almost always right because it
    is where you stop typing.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lines = src.splitlines()
    out = []
    # Top-level definitions want two blank lines after them; a method inside a
    # class wants one. Both are things ruff format rewrites.
    targets: list[tuple[ast.AST, int]] = [(n, 2) for n in tree.body]
    for parent in tree.body:
        if isinstance(parent, ast.ClassDef):
            # All but the LAST member. The gap after the last one belongs to
            # whatever top-level definition follows, which wants two -- so
            # checking it here reports every correctly formatted class.
            targets += [(n, 1) for n in parent.body[:-1]]

    # Two blank lines after the import block, before the first statement.
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    rest = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    if imports and rest:
        last_import = max(n.end_lineno or 0 for n in imports)
        first_other = min(n.lineno for n in rest if n.lineno > last_import)
        gap = [
            i for i in range(last_import, first_other - 1)
            if i < len(lines) and not lines[i].strip()
        ]
        if len(gap) != 2 and not isinstance(rest[0], ast.Expr):
            out.append(
                f"{path}:{last_import + 1}: {len(gap)} blank line(s) after the "
                "import block; ruff format wants 2"
            )

    for node, want in targets:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        end = node.end_lineno or 0
        blanks = 0
        i = end
        while i < len(lines) and not lines[i].strip():
            blanks += 1
            i += 1
        if i >= len(lines):
            continue  # end of file
        if blanks != want:
            where = "top-level" if want == 2 else "method"
            out.append(
                f"{path}:{end + 1}: {blanks} blank line(s) after {where} "
                f"{node.name!r}; ruff format wants {want}"
            )
    return out


def unsorted_import_statements(src: str, path: Path) -> list[str]:
    """Adjacent `from X import ...` lines that are not in module order.

    isort sorts a block of imports by module name, and ruff's I001 rewrites
    them. Only consecutive lines are compared, since a blank line starts a new
    group and groups have their own ordering rules.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    previous: ast.ImportFrom | None = None
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level:
            previous = None
            continue
        if previous is not None and (previous.end_lineno or 0) + 1 == node.lineno:
            if (node.module or "") < (previous.module or ""):
                out.append(
                    f"{path}:{node.lineno}: 'from {node.module} import ...' sorts "
                    f"before 'from {previous.module} import ...'"
                )
        previous = node
    return out


def check(path: Path) -> list[str]:
    src = path.read_text()
    lines = src.splitlines()
    out = [
        f"{path}:{n}: line is {len(line)} chars (limit {LIMIT})"
        for n, line in enumerate(lines, 1)
        if len(line) > LIMIT
    ]

    try:
        seq = units(src)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return out

    out += unused_imports(src, path)
    out += unsorted_import_names(src, path)
    out += unsorted_import_statements(src, path)
    out += blank_lines_after_top_level(src, path)

    for line, width in wrapped_but_fits(src, lines):
        out.append(
            f"{path}:{line}: wrapped call/signature would fit on one line "
            f"({width} chars) - ruff format will join it"
        )

    # Runs, not pairs. ruff joins an implicit concatenation only when the WHOLE
    # run fits on one line, so checking adjacent pairs reports a three-part
    # string whose first two happen to fit -- a false positive, and one that
    # would push an edit ruff then rejects from the other side.
    run: list[Unit] = []
    for unit in [*seq, None]:
        if unit is not None and not unit.block and (not run or unit.line != run[-1].line):
            run.append(unit)
            continue
        if len(run) > 1 and run[0].line != run[-1].line:
            indent = len(lines[run[0].line - 1]) - len(lines[run[0].line - 1].lstrip())
            merged = indent + len(run[0].text)
            for later in run[1:]:
                # joining drops the quotes and any prefix letters of each
                # continuation literal
                merged += len(later.text.lstrip("fFrRbB")) - 2
            if merged <= LIMIT:
                out.append(
                    f"{path}:{run[0].line}: split string would fit on one line "
                    f"({merged} chars) - ruff format will join it"
                )
        run = [unit] if unit is not None and not unit.block else []
    return out


def main() -> int:
    roots = [Path(a) for a in sys.argv[1:]] or [Path(".")]
    problems: list[str] = []
    for root in roots:
        files = sorted(root.rglob("*.py")) if root.is_dir() else [root]
        for f in files:
            if any(p in {".venv", "venv", "__pycache__", "node_modules"} for p in f.parts):
                continue
            problems += check(f)
    for p in problems:
        print(f"  {p}")
    print(f"\n  {len(problems)} issue(s)" if problems else "  clean")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
