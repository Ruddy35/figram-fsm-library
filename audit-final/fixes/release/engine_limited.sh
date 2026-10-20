#!/bin/sh
ulimit -v 3686400
exec bin/figram-engine --max-request 0 "$@"
