#!/usr/bin/env sh
# Every URL this stack serves, printed in one place.
#
# Prints only — it starts nothing. `make up` is the launcher; this is what you
# run afterwards, or a week later when you have forgotten which of the ten
# shops is which. By default it also probes each origin and marks what answers,
# so a blank browser tab is explained here rather than in the browser.
#
# Adding a shop means adding a row to SHOPS below, the same way it means adding
# a service to STORE_SERVICES in the Makefile. There is no discovery step: the
# script has no opinion about whether Docker is running and needs none.
#
#   ./links.sh              links, with a reachability mark against each shop
#   ./links.sh --no-check   links only, no network at all

set -eu

# domain|host-port|shop name. The port is the store container published on
# loopback; the domain is what the browser should actually use, because tap
# tokens and passkeys are bound to the origin the Consumer is looking at.
SHOPS='spoiledduckie.localhost|3000|SpoiledDuckie
dogeared.localhost|3010|Dog-Eared
circuityard.localhost|3020|CircuitYard
ironlist.localhost|3030|IronList
pantryline.localhost|3040|PantryLine
kettleandgrain.localhost|3050|Kettle & Grain
deskfield.localhost|3060|DeskField
rootandleaf.localhost|3070|Root & Leaf
playspool.localhost|3080|Playspool
furrow.localhost|3090|Furrow'

CHECK=1
for arg in "$@"; do
	case "$arg" in
		--no-check) CHECK=0 ;;
		-h|--help) sed -n '2,14p' "$0" | cut -c3- ; exit 0 ;;
		*) echo "links.sh: unknown option $arg (try --help)" >&2 ; exit 2 ;;
	esac
done

# Colour only when a human is looking; piping this into a file should give
# plain text.
if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
	B=$(printf '\033[1m'); DIM=$(printf '\033[2m'); OK=$(printf '\033[32m')
	WARN=$(printf '\033[33m'); R=$(printf '\033[0m')
else
	B=''; DIM=''; OK=''; WARN=''; R=''
fi

command -v curl >/dev/null 2>&1 || CHECK=0

# The edge is reached at 127.0.0.1 carrying the Host header rather than by
# resolving the name: browsers resolve *.localhost to loopback, and curl on
# some systems does not. Probing the way the Makefile does keeps a working
# stack from being reported as down.
probe() { # domain -> prints a marker
	[ "$CHECK" -eq 1 ] || return 0
	if curl -fsS -m 2 -H "Host: $1" http://127.0.0.1/ >/dev/null 2>&1; then
		printf '%s' "${OK}●${R} "
	else
		printf '%s' "${WARN}○${R} "
	fi
}

printf '\n%sOpenStore — every surface%s\n' "$B" "$R"
[ "$CHECK" -eq 1 ] && printf '%s● answering  ○ not answering%s\n' "$DIM" "$R"

printf '\n%sChat%s\n  ' "$B" "$R"
probe chat.localhost
printf 'http://chat.localhost\n'

printf '\n%sShops%s  %s(storefront · console · card · admin)%s\n' "$B" "$R" "$DIM" "$R"
echo "$SHOPS" | while IFS='|' read -r domain port name; do
	printf '\n  '
	probe "$domain"
	printf '%s%s%s\n' "$B" "$name" "$R"
	printf '    http://%s\n' "$domain"
	printf '    http://%s/agentic\n' "$domain"
	printf '    http://%s/.well-known/agent-commerce.json\n' "$domain"
	# The edge answers 404 to /admin on purpose (§6.1): the shop's own console
	# is session-authenticated and private-network only, so it is reached on
	# the store's published port instead of through Caddy.
	printf '    http://127.0.0.1:%s/admin%s   ← direct port, not the edge%s\n' "$port" "$DIM" "$R"
done

printf '\n%sAdmin sign-in%s  %soperator@<shop domain> / demo-operator-pw (seeded)%s\n' \
	"$B" "$R" "$DIM" "$R"
printf '%sPostgres%s      %s127.0.0.1:5432, user postgres%s\n' "$B" "$R" "$DIM" "$R"

printf '\n%s*.localhost resolves to loopback in Chrome and Firefox. A container does\n'   "$DIM"
printf 'not resolve it — that is SPEC §10.1. Nothing up? `make up`.%s\n\n' "$R"
