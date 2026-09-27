#!/bin/bash
set -e

# --- 1. Tests unitaires -----------------------------------------------
# set -e stoppe le script ici si pytest échoue, avant que le déploiement
# n'ait la moindre chance de s'exécuter.

python3 -m venv .venv
. .venv/bin/activate
pip install --quiet pytest
pytest tests/
deactivate

# --- 2. Déploiement -----------------------------------------------------
# Seuls les artefacts effectivement chargés par le navigateur :
#   * index.html, main.py, pyscript.toml à la racine
#   * les modules sous src/*.py
# Tout le reste (tests, scripts, pyproject.toml, READMEs, .git,
# .venv, __pycache__, …) est filtré par la chaîne include/exclude
# de rsync — et --delete retire du répertoire cible tout fichier
# qui n'existe plus dans le dépôt.

TARGET_DIR="/home/www/chords.ithaca.fr"

install -d -m 0755 "$TARGET_DIR"
rsync -av --delete \
      --chmod=D0755,F0644 \
      --include='/index.html' \
      --include='/main.py' \
      --include='/pyscript.toml' \
      --include='/src/' \
      --include='/src/*.py' \
      --exclude='*' \
      ./ "$TARGET_DIR/"

echo "Déployé sur https://chords.ithaca.fr"
