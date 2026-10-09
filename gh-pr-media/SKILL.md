---
name: gh-pr-media
description: Put screenshots, images or videos into GitHub pull requests, issues and comments from the shell. Use whenever a PR or issue should show a screenshot, before/after image or recording.
---

# Images and videos in GitHub PRs and issues

`gh` (2.101+) uploads media to GitHub's own attachment storage, the same as drag-and-drop in the web editor. There's no need to commit images, host them on a branch or ask the user to drag them in.

`--attach` works on `gh pr create`, `gh pr edit`, `gh pr comment`, `gh issue create` and `gh issue comment`. Pass one `--attach` per file, up to 50, in `<path>#<alt text>` form. Reference each file in the body by the same relative path, and `gh` rewrites the reference to the uploaded URL. Files the body doesn't reference are appended at the end.

```sh
cd /tmp/shots
gh pr create --title "..." \
  --attach './light.png#Header, light theme' \
  --attach './mobile.png#Header, mobile width' \
  --body "$(cat <<'EOF'
Summary...

**Desktop**
![Header, light theme](./light.png)

**Mobile**
![Header, mobile width](./mobile.png)
EOF
)"
```

`gh pr edit <n> --attach ...` with no `--body` keeps the existing body and appends the media. With `--body`, it replaces the body and rewrites the references, which is the way to swap in new screenshots.

Done when `gh pr view <n> --json body -q .body` shows `https://github.com/user-attachments/...` URLs in place of every local path.

## Without write access

`--attach` needs push access to the repository that owns the PR or issue. It fails with "attaching files requires write access to the repository", for example on a PR from your fork into someone else's repo. Check first with `gh api repos/<owner>/<repo> --jq .permissions.push`.

In that case, push the images to an orphan branch on your fork. Use git plumbing so the working tree stays untouched. Then embed `https://raw.githubusercontent.com/<you>/<repo>/<branch>/<file>`:

```sh
a=$(git hash-object -w light.png); b=$(git hash-object -w mobile.png)
t=$(printf "100644 blob $a\tlight.png\n100644 blob $b\tmobile.png\n" | git mktree)
git push -f origin "$(git commit-tree "$t" -m 'PR screenshots')":refs/heads/pr-screenshots
```

Tell the user the images live on that branch and disappear if it is deleted.

## Taking the screenshots

Capture the real rendered result: light and dark themes, plus a narrow viewport when layout changes. Look at every image before attaching it to confirm it shows the intended page. A port already in use can serve someone else's app.
