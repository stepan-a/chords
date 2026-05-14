<!-- 🌐 **English** · [Français](README.fr.md) -->

# Chords — guitar chord diagrams

A small piece of software that computes the playable positions of a chord on a
guitar fretboard and renders them as SVG grids. The engine is pure Python; the
UI runs in the browser via [PyScript](https://pyscript.net) (Pyodide /
WebAssembly), without a single line of hand-written JavaScript.

## Running it

PyScript fetches the `.py` files over HTTP, so you need a local server
(browsers block `fetch` from `file://`):

```bash
cd ~/works/chords
python -m http.server 8000
```

Then open <http://localhost:8000/> in a browser.

The first load downloads roughly 10 MB (the Pyodide runtime), which is then
cached — subsequent loads are near-instantaneous.

## Serving online with Apache2 on a sub-domain

The project consists entirely of static files (HTML plus Python read by the
browser; there is no server-side Python process). A vanilla Apache2 setup is
sufficient. HTTPS is recommended: Pyodide is larger than the average payload
and caching behaviour is friendlier in secure contexts (Service Workers,
`Cache-Control` more reliably honoured, no "mixed content" warnings).

The example below assumes a sub-domain `chords.example.com` served from
`/var/www/chords`.

### 1. Prepare the directory

```bash
sudo mkdir -p /var/www/chords
sudo chown -R "$USER:www-data" /var/www/chords
```

### 2. Deploy only what is needed

The browser runtime only needs:

- `index.html`, `main.py`, `pyscript.toml` at the root,
- the five modules inside `src/`.

Everything else (`tests/`, `scripts/`, `pyproject.toml`, `.git/`, etc.) has no
business being online. A targeted `rsync`:

```bash
cd ~/works/chords
rsync -av --delete \
  index.html main.py pyscript.toml \
  src/ \
  /var/www/chords/src/
# rsync places the contents of src/ inside /var/www/chords/src/ ;
# index.html, main.py and pyscript.toml go to /var/www/chords/ itself.
```

Alternatively, if you would rather deploy a Git clone:

```bash
sudo -u www-data git clone <your-repo> /var/www/chords
```

In that case the `<DirectoryMatch>` block below denies access to the
directories that should not be served.

### 3. VirtualHost

`/etc/apache2/sites-available/chords.conf`:

```apache
<VirtualHost *:80>
    ServerName chords.example.com
    DocumentRoot /var/www/chords

    <Directory /var/www/chords>
        Options -Indexes +FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>

    # Apache does not recognise .toml out of the box — declare it
    # explicitly so it is served as text rather than as application/
    # octet-stream.
    AddType application/toml .toml

    # The .py files are served as text (Apache will not execute them;
    # there is no mod_python here). This line is cosmetic but avoids
    # any ambiguity.
    AddType text/x-python .py

    # If you are serving a full Git clone, lock down what does not
    # belong online:
    <DirectoryMatch "/(tests|scripts|\.git)(/|$)">
        Require all denied
    </DirectoryMatch>
    <FilesMatch "^(pyproject\.toml|README(\..+)?\.md|\.gitignore)$">
        Require all denied
    </FilesMatch>

    ErrorLog  ${APACHE_LOG_DIR}/chords-error.log
    CustomLog ${APACHE_LOG_DIR}/chords-access.log combined
</VirtualHost>
```

Enable and reload:

```bash
sudo a2ensite chords.conf
sudo apache2ctl configtest
sudo systemctl reload apache2
```

### 4. DNS

Add an `A` (or `CNAME`) record for `chords.example.com` pointing at the
server's IP address.

### 5. HTTPS via Let's Encrypt

```bash
sudo apt install certbot python3-certbot-apache    # if not already present
sudo certbot --apache -d chords.example.com
```

Certbot automatically adds a `<VirtualHost *:443>` block that loads the
certificate and redirects port 80 to port 443. Renewal is handled by the
systemd timer `certbot.timer`.

### 6. Verify

```bash
curl -I https://chords.example.com/
curl -I https://chords.example.com/pyscript.toml
curl -I https://chords.example.com/src/notes.py
```

All three should respond with `200 OK`. Then open the page in a browser: the
"Loading…" banner disappears after a few seconds (first Pyodide load) and the
full UI appears.

### Caching (optional)

To speed up repeat visits, add the following to the VirtualHost:

```apache
<IfModule mod_expires.c>
    ExpiresActive On
    ExpiresByType text/html        "access plus 1 hour"
    ExpiresByType text/x-python    "access plus 1 day"
    ExpiresByType application/toml "access plus 1 day"
</IfModule>
```

(Requires `sudo a2enmod expires`.) During development, keep TTLs short or
disable this block entirely so you do not have to flush the cache after every
edit.

## Usage

- Type a chord progression in Anglo-Saxon notation into the text field,
  separated by spaces, commas or `|`: `Am F C G`, `Cmaj7 | D/F# | Bbm9`, …
- Pick a tuning from the drop-down.
- Pick what is drawn on each fingering dot: fingers (1–4), Roman-numeral
  degrees (I / III / V / VII / …), spelled notes (C / E / G / …), or nothing.
- Adjust the **Positions** slider for how many voicings each chord shows, and
  the **Variety** slider for how aggressively the search rejects look-alikes.
- Click a chord tile to expand its alternatives; click an alternative to
  make it active. Default voicings cascade from one chord to the next using a
  voice-leading distance metric, so the hand barely moves between chords; an
  explicit pick is sticky and won't be overwritten by downstream changes.
- Toggle between French and English with the FR / EN buttons in the header.

## Architecture

```
chords/
├── index.html          HTML page (UI)
├── pyscript.toml       PyScript configuration (file mounts)
├── main.py             Wire DOM ↔ Python engine
├── src/
│   ├── notes.py        Notes, intervals, enharmonic-aware spelling
│   ├── chords.py       Symbol parser + interval tables
│   ├── tunings.py      Tunings (Standard, DADGAD, Open G, …)
│   ├── voicings.py     Voicing search (DFS + dominance/diversity filters)
│   ├── render.py       SVG diagram rendering
│   └── i18n.py         FR/EN translation tables
├── tests/              pytest (172 tests at this point)
└── scripts/
    └── preview.py      Generates preview.html (offline visual sanity check)
```

## Development

```bash
source ~/.claude-venv/bin/activate
pytest                           # unit tests
python scripts/preview.py        # regenerate preview.html (static view)
```

## Supported notation

- Triads: `C`, `Cm`, `Cdim`, `Caug`, `Csus2`, `Csus4`
- Power chord: `C5`
- Sixths: `C6`, `Cm6`
- Sevenths: `C7`, `Cmaj7`, `Cm7`, `CmMaj7`, `Cdim7`, `Cm7b5` (alias `Cø`)
- Extensions: `C9`, `C11`, `C13`, `Cmaj9`, `Cm9`, `Cmaj11`, `Cm11`,
  `Cmaj13`, `Cm13`, `Cadd9`
- Alterations: `b5`, `#5`, `b9`, `#9`, `#11`, `b13`
- Slash (forced bass): `C/E`, `D/F#`, `Am7/G`

Alterations combine freely: `C7#5#9`, `C13b9`, `Cmaj7#11`, and so on.

## Theoretical decisions

- **Strict enharmonics.** `D` is spelled `D F# A` (never `D Gb A`); `Cdim7`
  comes out as `C Eb Gb Bbb` (rather than `C Eb Gb A`).
- **Strings indexed low-to-high** in the API; the renderer flips them at
  display time so the bass string sits on the left of the diagram, as in
  conventional chord charts.
- **Dominance filter.** When two voicings share the same fingering and the
  same bass but one mutes a string that could happily ring a chord tone,
  only the fuller voicing is kept.
- **Diversity filter.** After dominance, a greedy pass rejects candidates
  whose `voicing_distance` to an already-picked voicing falls below a
  threshold (with an extra penalty when the two have different bass strings).
  This is what stops the top-N from collapsing into five near-identical
  shapes at the nut.
- **Voice-leading defaults.** In a progression, each chord's default voicing
  is the candidate geometrically closest to the previously-active voicing,
  so the player's hand drifts as little as possible from one chord to the
  next. Manual picks are sticky and propagate forward in the cascade.
