#!/usr/bin/env python3
"""
Script di automazione (`cron_report.py`) eseguibile da terminale, crontab o GitHub Actions:
    python cron_report.py [--force]

Logica di frequenza intelligente:
- Legge `report_frequenza` dalla tabella `impostazioni` di Supabase (eseguendo sempre almeno
  una query al DB come keep-alive anti-pausa di Supabase).
- Se lanciato con `--force`, esegue sempre l'aggiornamento globale veloce e l'invio email.
- Se lanciato normalmente dal cron giornaliero:
  * `disattivato`: stampa "Report automatico disattivato nelle impostazioni. Uscita." e termina.
  * `settimanale` e oggi NON è lunedì (`datetime.now().weekday() != 0`):
    stampa "Frequenza settimanale: oggi non è lunedì, eseguito solo keep-alive DB." e termina.
  * `giornaliero` oppure lunedì (per `settimanale`): esegue `esegui_aggiornamento_globale_veloce()`
    e poi `invia_email_report()`.
"""

from datetime import datetime
import sys

from dotenv import load_dotenv

from db import get_impostazioni
from mailer import (
    esegui_aggiornamento_globale_veloce,
    genera_html_report,
    invia_email_report,
)


def _console_progress(pct: float, status_msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    pct_int = int(round(pct * 100))
    print(f"[{ts}] [{pct_int:3d}%] {status_msg}", flush=True)


def main() -> int:
    load_dotenv(override=True)
    force_run = "--force" in [arg.strip().lower() for arg in sys.argv[1:]]

    now = datetime.now()
    start_ts = now.strftime("%d/%m/%Y %H:%M:%S")
    print("=" * 72, flush=True)
    print(
        f"AVVIO CRON REPORT AUTOMATICO - {start_ts}"
        + (" [MODALITA' --force]" if force_run else ""),
        flush=True,
    )
    print("=" * 72, flush=True)

    # 1. Query a Supabase (funge sempre anche da Keep-Alive per il progetto Supabase)
    try:
        impostazioni = get_impostazioni(force_refresh=True)
    except Exception as exc:
        print(
            f"[ERRORE] Impossibile connettersi a Supabase per leggere `impostazioni`: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    raw_freq = (
        str(
            impostazioni.get("report_frequenza")
            or impostazioni.get("frequenza_aggiornamento")
            or "settimanale"
        )
        .strip()
        .lower()
    )
    if raw_freq in ("giornaliera", "giornaliero", "daily"):
        freq_norm = "giornaliero"
    elif raw_freq in ("disattivato", "disattivata", "disabled", "off", "false"):
        freq_norm = "disattivato"
    else:
        freq_norm = "settimanale"

    print(f"Frequenza configurata su Supabase: '{freq_norm}' (Giorno settimana: {now.weekday()})", flush=True)

    if not force_run:
        if freq_norm == "disattivato":
            print("Report automatico disattivato nelle impostazioni. Uscita.", flush=True)
            print("=" * 72, flush=True)
            return 0

        if freq_norm == "settimanale" and now.weekday() != 0:
            print(
                "Frequenza settimanale: oggi non è lunedì, eseguito solo keep-alive DB.",
                flush=True,
            )
            print("=" * 72, flush=True)
            return 0

    try:
        stats = esegui_aggiornamento_globale_veloce(progress_callback=_console_progress)
        p_stats = stats.get("prezzi", {})
        s_stats = stats.get("seo", {})
        b_stats = stats.get("backlinks", {})

        print("-" * 72, flush=True)
        print(
            f"Riepilogo Aggiornamento:\n"
            f"  - Prezzi aggiornati (Nostro Sito): {p_stats.get('miei_prezzi_aggiornati', 0)}\n"
            f"  - Prezzi aggiornati (Competitor):  {p_stats.get('comp_prezzi_aggiornati', 0)}\n"
            f"  - Keyword SEO analizzate:          {s_stats.get('keywords_analizzate', 0)}\n"
            f"  - Backlink verificati:             {b_stats.get('totale_verificati', 0)} "
            f"(Attivi: {b_stats.get('attivi', 0)}, Menzioni: {b_stats.get('menzioni', 0)}, Non attivi: {b_stats.get('non_attivi', 0)})",
            flush=True,
        )
        print("-" * 72, flush=True)
    except Exception as exc:
        print(f"[ERRORE] Fallito aggiornamento globale veloce: {exc}", file=sys.stderr, flush=True)
        return 1

    print("Generazione report HTML e invio email via SMTP in corso...", flush=True)
    html_content = genera_html_report()
    ok, msg = invia_email_report(html_content=html_content)

    if ok:
        print(f"[OK] {msg}", flush=True)
        print("=" * 72, flush=True)
        return 0

    print(f"[ERRORE INVIO EMAIL] {msg}", file=sys.stderr, flush=True)
    print("=" * 72, flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
