#!/bin/sh
# HMD_HERMES_CMD for a local Hermes profile: the server passes the prompt as $1.
printf '%s' "$1" | exec "${HERMES:-hermes}" -p chooser chat --query-file - -Q --format stream-json --max-turns 4 -t mcp-body
