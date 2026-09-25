#!/usr/bin/env python3
"""Add the comment overlay to one HTML file in local-only mode, inlined.

    bin/local-comments.py plan.html                 # writes plan.commentable.html
    bin/local-comments.py plan.html -o review.html

Same overlay as the shared-endpoint mode, so the same anchoring: every comment
records the quote plus the text either side of it, which is what lets a
one-word selection still be found in the source afterwards. The difference is
where records go. `data-endpoint="local"` keeps them in the browser's
localStorage under the project name, so they survive reloads and closing the
tab, and nothing is sent anywhere. A "Clear" button wipes them. A "Copy review for Claude" button hands the whole set
over as text.

No slug registry is involved: the project name only keys this browser's own storage,
so it is derived from the filename and never collides with anyone else's.

Rerunning on a page that already carries the layer replaces it.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(HERE, "assets", "html-comments.js")
CSS = os.path.join(HERE, "assets", "html-comments.css")
OPEN = "<!-- html-comments-local -->"
CLOSE = "<!-- /html-comments-local -->"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page", help="the HTML file to add the layer to")
    ap.add_argument("-o", "--out", help="where to write it (default: "
                                        "<name>.commentable.html)")
    ap.add_argument("--project", help="storage key for this tab "
                                      "(default: the file's name)")
    args = ap.parse_args()

    doc = open(args.page, encoding="utf-8").read()
    doc = re.sub(re.escape(OPEN) + ".*?" + re.escape(CLOSE), "", doc, flags=re.S)

    project = args.project or re.sub(
        r"[^a-z0-9-]+", "-",
        os.path.basename(args.page).rsplit(".", 1)[0].lower()).strip("-")
    if not project:
        sys.exit("could not derive a project name; pass --project")

    css = open(CSS, encoding="utf-8").read()
    js = open(JS, encoding="utf-8").read()
    # A literal </script> or </style> in the source would end the tag early.
    js = js.replace("</script", "<\\/script")
    css = css.replace("</style", "<\\/style")

    # The project may come from --project. Encode it for a JavaScript string
    # and keep '<' escaped so it cannot terminate the inline script tag.
    project_js = json.dumps(project).replace("<", "\\u003c")
    layer = (f"{OPEN}\n<style>\n{css}\n</style>\n"
             f'<script>window.HC_CONFIG = {{endpoint: "local", '
             f'project: {project_js}}};</script>\n'
             f"<script>\n{js}\n</script>\n{CLOSE}\n")

    if "</body>" in doc:
        doc = doc.replace("</body>", layer + "</body>", 1)
    else:
        doc += "\n" + layer

    out = args.out or re.sub(r"\.html?$", "", args.page) + ".commentable.html"
    open(out, "w", encoding="utf-8").write(doc)
    print(out)


if __name__ == "__main__":
    main()
