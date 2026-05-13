# Accords — diagrammes d'accords pour guitare

Petit logiciel qui calcule les positions jouables d'un accord sur un manche
de guitare et les affiche en grille SVG. Tout est pur Python ; l'UI tourne
dans le navigateur via [PyScript](https://pyscript.net) (Pyodide /
WebAssembly), sans une ligne de JavaScript écrite à la main.

## Lancer

PyScript va chercher les fichiers `.py` par HTTP, donc il faut un serveur
local (sinon les navigateurs bloquent les `fetch` depuis `file://`) :

```bash
cd ~/works/chords
python -m http.server 8000
```

Puis ouvrir <http://localhost:8000/> dans un navigateur.

Le premier chargement télécharge ~10 Mo (le runtime Pyodide), mis en cache
ensuite — les chargements suivants sont quasi instantanés.

## Utilisation

- Tape un symbole d'accord en notation anglo-saxonne dans le champ texte :
  `Cmaj7`, `F#m7b5`, `D/F#`, `Bbm9`, `C13b9`, …
- Choisis l'accordage dans le menu déroulant.
- Choisis ce qui s'affiche sur les points : doigts (1-4), degrés (chiffres
  romains I/III/V/VII/…), notes (C/E/G/…), ou rien.

## Architecture

```
chords/
├── index.html          Page HTML (UI)
├── pyscript.toml       Configuration PyScript (montage des modules)
├── main.py             Wire-up DOM ↔ moteur Python
├── src/
│   ├── notes.py        Notes, intervalles, enharmoniques
│   ├── chords.py       Parser de symboles + tables d'intervalles
│   ├── tunings.py      Accordages (Standard, DADGAD, Open G…)
│   ├── voicings.py     Recherche de positions jouables (DFS + filtrage)
│   └── render.py       Génération SVG des grilles
├── tests/              Pytest (143 tests à ce stade)
└── scripts/
    └── preview.py      Génère preview.html (test visuel hors-navigateur)
```

## Développement

```bash
source ~/.claude-venv/bin/activate
pytest                           # tests unitaires
python scripts/preview.py        # régénère preview.html (vue statique)
```

## Notation acceptée

- Triades : `C`, `Cm`, `Cdim`, `Caug`, `Csus2`, `Csus4`
- Power chord : `C5`
- Sixtes : `C6`, `Cm6`
- Septièmes : `C7`, `Cmaj7`, `Cm7`, `CmMaj7`, `Cdim7`, `Cm7b5` (alias `Cø`)
- Extensions : `C9`, `C11`, `C13`, `Cmaj9`, `Cm9`, `Cmaj11`, `Cm11`,
  `Cmaj13`, `Cm13`, `Cadd9`
- Altérations : `b5`, `#5`, `b9`, `#9`, `#11`, `b13`
- Slash (basse imposée) : `C/E`, `D/F#`, `Am7/G`

Les altérations se combinent : `C7#5#9`, `C13b9`, `Cmaj7#11`, etc.

## Choix théoriques

- **Enharmoniques stricts** : `D` est épelé `D F# A` (pas `D Gb A`),
  `Cdim7` rend `C Eb Gb Bbb` (et non `C Eb Gb A`).
- **Cordes indexées graves→aigües** dans l'API ; le rendu inverse pour la
  convention de lecture habituelle (corde grave à gauche du diagramme).
- **Filtrage des voicings dominés** : si deux positions ont le même doigté
  et la même basse mais l'une mute une corde qui pourrait sonner une note
  de l'accord, seule la plus complète est retenue.
