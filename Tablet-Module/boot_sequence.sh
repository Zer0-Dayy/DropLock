#!/bin/bash
set -euo pipefail

source /home/zero-day/Projects/DropLock/Tablet-Module/env.sh
cd /home/zero-day/Projects/DropLock/Tablet-Module

exec /home/zero-day/Projects/DropLock/Tablet-Module/.venv/bin/python /home/zero-day/Projects/DropLock/Tablet-Module/main.py
