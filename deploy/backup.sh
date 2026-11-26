#!/bin/sh
set -eu
umask 077
cd "$(dirname "$0")/.."
if [ "$#" -ne 1 ]; then
    echo 'Usage: deploy/backup.sh /absolute/path/backup.tar.gz' >&2
    exit 2
fi
case "$1" in
    /*) destination=$1 ;;
    *) echo 'Use an absolute backup path' >&2; exit 2 ;;
esac
test ! -e "$destination"
temporary=$(mktemp "${destination}.XXXXXX")
cleanup() {
    rm -f -- "$temporary"
    docker compose --env-file .env.host start app
}
trap cleanup EXIT
docker compose --env-file .env.host stop app
docker compose --env-file .env.host run --rm --no-deps -T --entrypoint tar app \
    -C /data --exclude=./models -czf - . > "$temporary"
tar -tzf "$temporary" >/dev/null
mv -n "$temporary" "$destination"
echo "Backup written to $destination"
