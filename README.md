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

## Servir en ligne avec Apache2 sur un sous-domaine

Le projet est constitué uniquement de fichiers statiques (HTML + Python lus
par le navigateur, pas de processus serveur Python). Une configuration
Apache2 classique suffit. HTTPS est recommandé : Pyodide est plus gros que
la moyenne et la mise en cache profite des contextes sécurisés (Service
Workers, `Cache-Control` plus respectés, pas de warning « mixed content »).

L'exemple suppose un sous-domaine `chords.example.com` servi depuis
`/var/www/chords`.

### 1. Préparer le répertoire

```bash
sudo mkdir -p /var/www/chords
sudo chown -R "$USER:www-data" /var/www/chords
```

### 2. Déployer uniquement le strict nécessaire

Le runtime navigateur n'a besoin que de :

- `index.html`, `main.py`, `pyscript.toml` à la racine,
- les cinq modules dans `src/`.

Le reste (`tests/`, `scripts/`, `pyproject.toml`, `.git/`, etc.) n'a rien
à faire en ligne. Un `rsync` ciblé :

```bash
cd ~/works/chords
rsync -av --delete \
  index.html main.py pyscript.toml \
  src/ \
  /var/www/chords/src/
# rsync place les fichiers de src/ dans /var/www/chords/src/ ;
# index.html, main.py et pyscript.toml vont à la racine /var/www/chords/.
```

Variante plus simple si tu préfères déployer un clone du dépôt :

```bash
sudo -u www-data git clone <ton-dépôt> /var/www/chords
```

Dans ce cas, le `<DirectoryMatch>` plus bas refuse l'accès aux dossiers
qui n'ont pas à être servis.

### 3. VirtualHost

`/etc/apache2/sites-available/chords.conf` :

```apache
<VirtualHost *:80>
    ServerName chords.example.com
    DocumentRoot /var/www/chords

    <Directory /var/www/chords>
        Options -Indexes +FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>

    # Apache ne connaît pas .toml par défaut — déclaré explicitement
    # pour qu'il soit servi en text/plain au lieu d'application/octet-stream.
    AddType application/toml .toml

    # Les .py sont servis comme texte (Apache ne les exécute pas, on n'a
    # pas mod_python ; cette ligne est cosmétique mais évite les ambiguïtés).
    AddType text/x-python .py

    # Si tu héberges depuis un clone Git complet, bloque ce qui n'a rien
    # à faire en ligne :
    <DirectoryMatch "/(tests|scripts|\.git)(/|$)">
        Require all denied
    </DirectoryMatch>
    <FilesMatch "^(pyproject\.toml|README\.md|\.gitignore)$">
        Require all denied
    </FilesMatch>

    ErrorLog  ${APACHE_LOG_DIR}/chords-error.log
    CustomLog ${APACHE_LOG_DIR}/chords-access.log combined
</VirtualHost>
```

Active et recharge :

```bash
sudo a2ensite chords.conf
sudo apache2ctl configtest
sudo systemctl reload apache2
```

### 4. DNS

Ajouter un enregistrement `A` (ou `CNAME`) pour `chords.example.com`
pointant vers l'IP du serveur.

### 5. HTTPS via Let's Encrypt

```bash
sudo apt install certbot python3-certbot-apache    # si pas déjà installé
sudo certbot --apache -d chords.example.com
```

Certbot ajoute automatiquement un `<VirtualHost *:443>` qui charge le
certificat et redirige le port 80 vers le 443. Renouvellement géré par
le timer systemd `certbot.timer`.

### 6. Vérifier

```bash
curl -I https://chords.example.com/
curl -I https://chords.example.com/pyscript.toml
curl -I https://chords.example.com/src/notes.py
```

Tous doivent répondre `200 OK`. Ouvre ensuite la page dans un navigateur :
le bandeau « Chargement… » disparaît au bout de quelques secondes (premier
chargement de Pyodide) et tu obtiens l'UI complète.

### Mise en cache (optionnel)

Pour accélérer les visites répétées, ajouter dans le VirtualHost :

```apache
<IfModule mod_expires.c>
    ExpiresActive On
    ExpiresByType text/html       "access plus 1 hour"
    ExpiresByType text/x-python   "access plus 1 day"
    ExpiresByType application/toml "access plus 1 day"
</IfModule>
```

(Nécessite `sudo a2enmod expires`.) Pendant le développement, garde des
TTL courts ou désactive ce bloc pour ne pas avoir à vider le cache à
chaque modification.

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
