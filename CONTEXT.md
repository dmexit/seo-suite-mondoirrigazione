# CONTEXT.md — Memoria Tecnica Permanente del Progetto (SEO, Prezzi & Backlink Suite)

Applicazione web multi-pagina in **Python + Streamlit** collegata a **Supabase (PostgreSQL + Auth)** per il monitoraggio competitivo di **Prezzi**, **Posizionamento SEO (SERP Google.it)** e **Backlink & Menzioni Brand** dell'e-commerce **Mondoirrigazione** (`https://www.mondoirrigazione.it`).

---

## 1. Stack Tecnologico & Architettura dei File

- **Runtime & Deploy:**
  - Sviluppo locale: `.venv/bin/python` — avvio web: `.venv/bin/streamlit run app.py --server.port 8501`.
  - Produzione Web: **Streamlit Community Cloud** (legge le chiavi da `st.secrets` o variabili d'ambiente tramite `get_env_or_secret()` in `db.py` e `_get_secret_safe()` in `scraper.py`).
  - Automazione & Keep-Alive Cloud: **GitHub Actions** (`.github/workflows/automazione_suite.yml`) + script CLI `cron_report.py`.
- **Frontend / UI:** Streamlit (`st.navigation` multi-page, tema Dark moderno definito in `.streamlit/config.toml`, grafici interattivi `plotly`, anteprima report HTML via `st.components.v1.html`).
- **Database & Auth:** Supabase (`supabase-py`, autenticazione email/password con RLS + `SUPABASE_SERVICE_ROLE_KEY` per gestione utenti admin e script CLI/cron).
- **Motore di Scraping & AI:**
  - `curl_cffi` (`impersonate="chrome120"`) + fallback `requests` e `BeautifulSoup4` (`lxml` / `html.parser`) per superare protezioni anti-bot / Cloudflare / Amazon.
  - **Serper.dev (`SERPER_API_KEY`)**: Google Search API (`gl="it"`, `hl="it"`) usata come priorità assoluta sia per l'Auto-Discovery dei link prodotto sia per le rilevazioni SERP Top 100.
  - **Google Gemini (`GEMINI_API_KEY` con SDK `google-genai`, modello `gemini-2.5-flash` / `gemini-3.8-flash`)**: disambiguazione intelligente degli URL prodotto, fallback estrazione prezzi e stima dei volumi di ricerca mensili (bucket standard Google Ads).

### Mappa dei Moduli Principali
| File | Ruolo e Responsabilità |
| :--- | :--- |
| `app.py` | Entrypoint Streamlit: autenticazione Supabase (`render_login`), caricamento brand da `impostazioni`, sidebar minimale (logo, navigazione `st.navigation`, pulsante `Esci`). |
| `db.py` | Layer dati centralizzato verso Supabase (funziona sia in contesto Streamlit con `st.session_state` e `st.secrets` sia in modalità headless/CLI per `cron_report.py`). Include il parser universale `read_csv_flexible` (accetta `,` e `;`, converte vuoti/`NaN` in `None` e `mio_prezzo` vuoto in `0.0`). |
| `scraper.py` | Motore parallelo (`ThreadPoolExecutor(max_workers=6)`) per: <br>1. Auto-Discovery URL e scraping prezzi e-commerce (Amazon, PrestaShop/Agricolbio, VirtueMart/Puntoirrigazione, WooCommerce, Magento, JSON-LD, Microdata).<br>2. Analisi SERP Top 100 (`analizza_serp_keyword`), decodifica redirect Google Merchant (`unwrap_google_url`) e stima volumi (`stima_volume_ricerca`).<br>3. Verifica stato Backlink e Menzioni Brand (`verifica_singolo_backlink`: Caso A Link `<a>` con `dofollow`/`nofollow`/`sponsored`; Caso B Menzione testuale del brand senza link con `rel_attribute = "menzione"` e snippet contestuale max 90 char). |
| `mailer.py` | Generazione report HTML responsive con CSS inline (`genera_html_report`: Top 20 offerte competitor più aggressive con colonna Evoluzione `▲`/`▼`/`=`, Top 20 keyword SEO, riepilogo Backlink & Menzioni brand), invio email multi-destinatario via SMTP/STARTTLS/SSL (`invia_email_report`) e orchestratore veloce (`esegui_aggiornamento_globale_veloce`). |
| `cron_report.py` | Script CLI autonomo (`python cron_report.py [--force]`): legge `report_frequenza` da `impostazioni` (`giornaliero`, `settimanale` solo di lunedì, `disattivato`), mantiene attivo il DB Supabase e avvia aggiornamento veloce + invio report email. |
| `.github/workflows/automazione_suite.yml` | Workflow GitHub Actions: gira ogni giorno alle `06:30 UTC` e `18:30 UTC` (più `workflow_dispatch` manuale). Sveglia l'app su Streamlit Cloud (`STREAMLIT_APP_URL`) ed esegue `python cron_report.py` nel turno mattutino. |
| `views/prezzi.py` | **Modulo 1 — Prezzi & Competitor**: Tab 1 Report & Posizionamento (Sintesi per Categoria e Marchio + vista Dettaglio con Diff % ed **Evoluzione vs prec.** `▲`/`▼`/`=`), Tab 2 Gestione Prodotti (form manuale, import CSV flessibile, export CSV catalogo completo, modifica/elimina popup), Tab 3 Gestione Competitor & Link (pulsanti *Aggiorna Solo Prezzi Veloce* e *Cerca Link Mancanti + Aggiorna Prezzi*, lista prodotti con Diff %, Evoluzione e dialog modifica). |
| `views/seo.py` | **Modulo 2 — SEO**: Tab 1 Report Posizionamento (KPI, filtri, pallini colorati per posizione e variazione, **card di dettaglio inline** sotto la riga cliccata con confronto competitor, grafico storico Plotly con asse Y invertito `100 -> 1` e **Top 5 Attuale in SERP**), Tab 2 Gestione Keyword & CSV, Tab 3 Competitor SEO. |
| `views/backlink.py` | **Modulo 3 — Backlink & Menzioni**: Tab 1 Monitoraggio & Stato (5 KPI inclusi **Link Attivi** e **Menzioni No Link**, verifica parallela, badge stato Attivo/Non attivo con diagnostica tecnica dell'errore HTTP/link, badge `rel` incluso `Menzione (No Link)`, azioni rapide per riga), Tab 2 Gestione Link & CSV. |
| `views/impostazioni.py` | **Modulo 4 — Impostazioni**: Tab 1 Generali & Brand + Gestione Utenti Supabase Auth, Tab 2 Report Email & Automazione (configurazione SMTP, invio report istantaneo, aggiorna tutto veloce + invio, anteprima HTML in-app e istruzioni `crontab`). |

---

## 2. Schema Esatto delle Tabelle Supabase (PostgreSQL)

> **REGOLA CRITICA SUGLI ID:** Tutte le tabelle (eccetto `impostazioni` che usa `id = 1` intero) utilizzano **`UUID` (`str`)** come chiave primaria `id` e come chiavi esterne (`prodotto_id`, `competitor_id`, `keyword_id`). **NON convertire mai gli `id` con `int(...)`** — usa sempre `str(...)`.

1. **`impostazioni`** *(riga singola `id = 1`)*:
   - `id` (`INT`, PK = `1`), `nome_azienda` (`TEXT`), `sito_web` (`TEXT`), `logo_url` (`TEXT`), `frequenza_aggiornamento` (`TEXT`), `invia_report_email` (`BOOLEAN`), `email_destinatari` (`TEXT`), `smtp_host` (`TEXT`), `smtp_port` (`INT`), `smtp_user` (`TEXT`), `smtp_password` (`TEXT`), `report_frequenza` (`TEXT`: `'Settimanale'`, `'Giornaliero'`, `'Disattivato'`), `ultimo_aggiornamento` (`TIMESTAMPTZ`).
2. **`prodotti`**:
   - `id` (`UUID`, PK), `sku` (`TEXT`, nullable, unique quando presente), `descrizione` (`TEXT`, obbligatorio), `categoria` (`TEXT`), `marchio` (`TEXT`), `mio_prezzo` (`NUMERIC`, default `0.0`), `mio_url` (`TEXT`, nullable), `created_at` (`TIMESTAMPTZ`).
3. **`competitors`**:
   - `id` (`UUID`, PK), `nome` (`TEXT`), `dominio` (`TEXT`), `attivo_prezzi` (`BOOLEAN`, default `False`), `attivo_seo` (`BOOLEAN`, default `False`), `created_at` (`TIMESTAMPTZ`).
4. **`prodotti_competitor`**:
   - `id` (`UUID`, PK), `prodotto_id` (`UUID` FK -> `prodotti.id`), `competitor_id` (`UUID` FK -> `competitors.id`), `url_prodotto` (`TEXT`, nullable), `ultimo_prezzo` (`NUMERIC`, nullable), `prezzo_precedente` (`NUMERIC`, nullable), `ultimo_check` (`TIMESTAMPTZ`). Vincolo unique su `(prodotto_id, competitor_id)`.
5. **`storico_prezzi`**:
   - `id` (`UUID`, PK), `prodotto_id` (`UUID` FK), `competitor_id` (`UUID` FK), `prezzo` (`NUMERIC`), `data_rilevazione` (`TIMESTAMPTZ`).
6. **`seo_keywords`**:
   - `id` (`UUID`, PK), `keyword` (`TEXT`, unique), `volume_mensile` (`INT`, nullable), `mia_posizione_attuale` (`INT`, nullable, `1..100` o `NULL` se `>100`), `mia_posizione_precedente` (`INT`, nullable), `top_5_serp` (`JSONB`, default `'[]'::jsonb`, lista di `{"pos", "title", "link", "domain"}`), `ultimo_check` (`TIMESTAMPTZ`).
7. **`seo_storico`**:
   - `id` (`UUID`, PK), `keyword_id` (`UUID` FK -> `seo_keywords.id`), `competitor_id` (`UUID` FK -> `competitors.id`, nullable se `is_mio_sito = True`), `is_mio_sito` (`BOOLEAN`), `posizione` (`INT`, nullable), `url_posizionato` (`TEXT`, nullable), `data_rilevazione` (`TIMESTAMPTZ`).
8. **`backlinks`**:
   - `id` (`UUID`, PK), `url_sorgente` (`TEXT`), `url_destinazione` (`TEXT`, nullable), `anchor_text` (`TEXT`, nullable), `rel_attribute` (`TEXT`, nullable: `dofollow`, `nofollow`, `sponsored`, `ugc`, `menzione`), `stato_attivo` (`BOOLEAN`, default `False`), `http_status` (`INT`, nullable), `ultimo_check` (`TIMESTAMPTZ`).

---

## 3. Regole UI, Codice e Performance (Obbligatorie)

1. **Design & Icone (Zero Emoji):**
   - Stile minimal dark lineare.
   - **Vietato usare emoji colorate** nel codice o nei testi UI. Usare esclusivamente le **Google Material Symbols** native di Streamlit con sintassi `:material/nome_icona:`. Nei report HTML/tabelle usare simboli tipografici puliti (`▲`, `▼`, `=`).
2. **Concorrenza & Thread-Safety in Streamlit:**
   - Tutte le scansioni massive usano `ThreadPoolExecutor(max_workers=6)` con `wait(..., timeout=..., return_when=FIRST_COMPLETED)` e `executor.shutdown(wait=False, cancel_futures=True)` per impedire blocchi su siti lenti.
   - **Nessun worker secondario deve mai chiamare funzioni `st.*` o accedere a `st.session_state`**. L'aggiornamento di `st.progress` e il salvataggio su DB tramite `db.py` avvengono esclusivamente nel ciclo `for future in done:` sul thread principale.
3. **Anti-Bot & Timeout Rigorosi:**
   - Usare sempre `curl_cffi.requests.get(..., impersonate="chrome120", allow_redirects=True)` con timeout stretti (`5s` per prezzi, `10s` per backlink) e fallback automatico a `requests.get`.
   - Leggere le chiavi segrete tramite `get_env_or_secret` / `_get_secret_safe` (compatibile con `.env`, GitHub Actions Secrets e `st.secrets` di Streamlit Cloud).
   - Per il calcolo posizioni SEO e Top 5 SERP, considerare **solo risultati organici puri** (`organic` di Serper.dev) e decodificare sempre eventuali URL Google Merchant (`google.com/goto?url=CAES...`) tramite `unwrap_google_url()`.
4. **Regola di Avanzamento Prezzi / Posizioni Precedenti:**
   - Prima di sovrascrivere `ultimo_prezzo` o `mia_posizione_attuale`, copiare il valore precedente in `prezzo_precedente` / `mia_posizione_precedente` (alla primissima rilevazione, se il precedente è `NULL`, inizializzarlo uguale al valore attuale così l'evoluzione iniziale parte da `= Stabile` / `0`).
