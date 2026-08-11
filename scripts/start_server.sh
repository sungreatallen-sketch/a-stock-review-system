#!/bin/bash
cd "$(dirname "$0")/.."
exec .venv/bin/python run_cli.py serve
