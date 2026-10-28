#!/bin/sh
# Delete uploaded source footage and YouTube working files older than 14 days. Finished clips are kept.
find /srv/clip-data/campaigns \( -path "*/assets/*" -o -path "*/_work/*" \) -type f -mtime +14 -delete
