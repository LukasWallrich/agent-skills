# Deploying your own endpoint

The overlay stores comments via a Google Apps Script web app that appends rows to a
Google Sheet (one tab per `project`). `Code.gs` here is the endpoint source. It fixes two
defects of the older shared FORRT endpoint: tab names are sanitised identically on read and
write, and a missing `project` or non-JSON body is rejected instead of appending a blank row
to a junk `default` tab. It also adds `?action=projects` (list existing tabs).

Fully scriptable with the `clasp` CLI (`npm i -g @google/clasp`, `clasp login` once):

```bash
# 1. Create the backing sheet (note its id from the URL), then:
mkdir hc-endpoint && cd hc-endpoint
clasp create --type standalone --title "html-comments endpoint" --rootDir .
# 2. Copy Code.gs here, set SPREADSHEET_ID at its top to your sheet's id, and write
#    appsscript.json with a webapp block:
#    { "timeZone": "Europe/London", "runtimeVersion": "V8",
#      "webapp": { "executeAs": "USER_DEPLOYING", "access": "ANYONE_ANONYMOUS" },
#      "oauthScopes": ["https://www.googleapis.com/auth/spreadsheets"] }
clasp push -f
clasp deploy -d "html-comments endpoint"
# -> deployment id AKfyc…; the endpoint URL is
#    https://script.google.com/macros/s/<DEPLOYMENT_ID>/exec
```

One manual step: the first run needs OAuth consent (the script runs as you). Open the
script editor (`clasp open-script` or the URL clasp printed), click **Run**, and approve the
authorization dialog. Then verify:

```bash
curl -sL '<ENDPOINT>?action=ping'    # -> {"ok":true,"service":"html-comments",...}
```

Put the `/exec` URL in `~/.claude/html-comments.config.json` (see the skill's README /
config.example.json). The URL is public and unauthenticated — anyone who has it can read
and write the sheet, so don't share it and don't use it for sensitive content.

Set `ADMIN_TOKEN` at the top of Code.gs to a random string (`python3 -c "import secrets;
print(secrets.token_urlsafe(24))"`) and put the same value in `adminToken` in your config.
It gates the `release` action only; leave both empty and releasing is unavailable. Never
commit the real value — the repo copy keeps a placeholder.

To change server behaviour later: edit Code.gs, `clasp push -f`, then `clasp deploy -i
<existing deployment id>` to keep the same URL. Using `-i` matters: a fresh deployment gets
a new URL, and every published page embeds the old one.
