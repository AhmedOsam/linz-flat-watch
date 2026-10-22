# linz-flat-watch

Checks willhaben and the Linz Genossenschaften every 10 minutes for rentals near JKU and sends each new one to WhatsApp.

Filters (`config.json`): within `max_km` of JKU, Miete + BK up to `max_rent`, optional `min_size` in m2.
A listing with a postcode must be in `near_plz`; one with coordinates must also be within `max_km`. Listings with neither are matched by place names in `near_places`.

Sources: willhaben (Linz, Urfahr-Umgebung), genossenschaften.immo (GWG, Lebensraeume, BRW, GIWOG, Familie, VLW, ...), GWG Linz, WSG, Neue Heimat OOE.

## Alert channels

Set any of these as repository secrets (Settings > Secrets and variables > Actions). Every configured channel gets each alert. Nothing personal lives in the code; the phone number and addresses exist only as encrypted Actions secrets.

### Email

| Secret | Example |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `465` (optional, SSL) |
| `SMTP_USER` | sending account |
| `SMTP_PASS` | app password (Gmail: Google account > Security > App passwords) |
| `MAIL_TO` | recipient address |
| `MAIL_FROM` | optional, defaults to `SMTP_USER` |

### WhatsApp (CallMeBot)

1. On the receiving phone, add the bot number shown at https://www.callmebot.com/blog/free-api-whatsapp-messages/ as a contact.
2. Send it `I allow callmebot to send me messages` and wait for the API key.
3. Add secrets `CALLMEBOT_PHONE` (with country code) and `CALLMEBOT_APIKEY`.

Test any channel: Actions > watch > Run workflow, or locally `python3 watch.py --test` with the same variables set.

## State

`state/seen.json` is committed by the workflow. It records every listing seen, so each flat alerts once. A source's first successful run is recorded silently, so the first run (or a source coming back after an outage) does not flood. If a source fails for 12 runs in a row the run turns red and GitHub emails the repo owner.
