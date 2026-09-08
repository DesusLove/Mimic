#!/usr/bin/env sh
# One-command install for mimic. Installs uv if missing, then mimic (isolated).
set -e

if ! command -v uv >/dev/null 2>&1; then
  echo "installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Install from the current directory if run inside the repo, else from PyPI.
if [ -f "pyproject.toml" ] && grep -q 'name = "mimic-client"' pyproject.toml 2>/dev/null; then
  uv tool install . --reinstall
else
  uv tool install mimic-client --reinstall
fi

echo
echo "installed. next:"
echo "  mimic doctor      # check setup"
echo "  mimic record      # start capture, follow the iPhone steps"
