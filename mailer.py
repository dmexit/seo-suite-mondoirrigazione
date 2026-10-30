from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html as html_lib
import os
import re
import smtplib
from typing import Any, Callable, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from db import (
    get_backlinks,
    get_competitors_prezzi,
    get_impostazioni,
    get_prodotti,
    get_prodotti_competitor,
    get_seo_keywords,
)
from scraper import (
    clean_domain,
    esegui_aggiornamento_seo,
    esegui_auto_discovery_e_scraping,
    esegui_verifica_backlinks,
)

load_dotenv(override=True)


# =========================================================================
# 1. AGGIORNAMENTO GLOBALE VELOCE (SOLO PREZZI + SEO + BACKLINK)
# =========================================================================


def esegui_aggiornamento_globale_veloce(
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Dict[str, Any]:
    """
    Esegue in sequenza (usando i worker multi-thread di `scraper.py`):
    a) Aggiornamento Solo Prezzi (Veloce): aggiorna in parallelo `mio_prezzo` (da `mio_url`)
       e `ultimo_prezzo` dei competitor (da `url_prodotto`) SOLO per gli URL già salvati,
       saltando completamente la ricerca di link mancanti (`discover_missing_urls=False`).
    b) Aggiornamento Posizionamenti SEO: aggiorna posizioni SERP (nostro sito + competitor SEO + Top 5)
       per tutte le keyword in `seo_keywords`.
    c) Verifica Backlink: controlla tutti i backlink in `backlinks`.
    """
    load_dotenv(override=True)

    def _cb_prezzi(pct: float, msg: str) -> None:
        if progress_callback:
            progress_callback(min(0.40, pct * 0.40), f"[1/3 Prezzi Veloce] {msg}")

    def _cb_seo(pct: float, msg: str) -> None:
        if progress_callback:
            progress_callback(
                min(0.75, 0.40 + pct * 0.35),
                f"[2/3 Posizionamento SEO] {msg}",
            )

    def _cb_backlink(pct: float, msg: str) -> None:
        if progress_callback:
            progress_callback(
                min(1.00, 0.75 + pct * 0.25),
                f"[3/3 Verifica Backlink] {msg}",
            )

    if progress_callback:
        progress_callback(0.02, "[1/3 Prezzi Veloce] Avvio lettura prezzi dai link salvati...")

    stats_prezzi = esegui_auto_discovery_e_scraping(
        progress_callback=_cb_prezzi,
        discover_missing_urls=False,
    )

    if progress_callback:
        progress_callback(0.40, "[2/3 Posizionamento SEO] Avvio scansione SERP Google.it...")

    stats_seo = esegui_aggiornamento_seo(progress_callback=_cb_seo)

    if progress_callback:
        progress_callback(0.75, "[3/3 Verifica Backlink] Avvio verifica stato backlink...")

    stats_backlinks = esegui_verifica_backlinks(progress_callback=_cb_backlink)

    if progress_callback:
        progress_callback(1.00, "Aggiornamento globale veloce completato!")

    return {
        "prezzi": stats_prezzi,
        "seo": stats_seo,
        "backlinks": stats_backlinks,
    }


# =========================================================================
# 2. GENERAZIONE REPORT HTML PROFESSIONALE (INLINE CSS)
# =========================================================================


def _esc(val: Any) -> str:
    """Escape HTML sicuro per stringhe nel report."""
    if val is None:
        return ""
    return html_lib.escape(str(val))


def _calc_diff_pct(mio_prezzo: Any, comp_prezzo: Any) -> Optional[float]:
    try:
        if mio_prezzo is None or comp_prezzo is None:
            return None
        m = float(mio_prezzo)
        c = float(comp_prezzo)
        if m <= 0 or c <= 0:
            return None
        return ((c - m) / m) * 100.0
    except (ValueError, TypeError):
        return None


def _render_diff_badge_inline(diff_val: Optional[float]) -> str:
    if diff_val is None:
        return '<span style="color:#94a3b8;font-size:12px;">N/D</span>'
    sign = "+" if diff_val > 0 else ""
    if diff_val < 0:
        return (
            f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;'
            f'background-color:#fee2e2;color:#b91c1c;font-weight:700;font-size:12px;">'
            f"{sign}{diff_val:.1f}%</span>"
        )
    return (
        f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;'
        f'background-color:#dcfce7;color:#15803d;font-weight:700;font-size:12px;">'
        f"{sign}{diff_val:.1f}%</span>"
    )


def _render_evoluzione_badge_inline(
    ultimo_prezzo: Any,
    prezzo_precedente: Any,
) -> str:
    """
    Confronta `ultimo_prezzo` con `prezzo_precedente`:
    - Aumento (`ultimo_prezzo > prezzo_precedente`): `▲ +€ X.XX (+Y.Y%)` (rosso/arancio)
    - Diminuzione (`ultimo_prezzo < prezzo_precedente`): `▼ -€ X.XX (-Y.Y%)` (verde/azzurro)
    - Uguale o `prezzo_precedente` None: `= Stabile`
    """
    try:
        if ultimo_prezzo is None or float(ultimo_prezzo) <= 0:
            return '<span style="color:#94a3b8;font-size:12px;">N/D</span>'
        curr = round(float(ultimo_prezzo), 2)
        if prezzo_precedente is None or float(prezzo_precedente) <= 0:
            return '<span style="color:#64748b;font-size:12px;font-weight:600;">= Stabile</span>'
        prev = round(float(prezzo_precedente), 2)
        delta = round(curr - prev, 2)
        if abs(delta) < 0.01:
            return '<span style="color:#64748b;font-size:12px;font-weight:600;">= Stabile</span>'
        pct = ((curr - prev) / prev) * 100.0
        if delta > 0:
            return (
                f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;'
                f'background-color:#ffedd5;color:#c2410c;font-weight:700;font-size:11px;">'
                f"&#9650; +&euro; {delta:.2f} (+{pct:.1f}%)</span>"
            )
        return (
            f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;'
            f'background-color:#e0f2fe;color:#0369a1;font-weight:700;font-size:11px;">'
            f"&#9660; -&euro; {abs(delta):.2f} ({pct:.1f}%)</span>"
        )
    except (ValueError, TypeError):
        return '<span style="color:#64748b;font-size:12px;font-weight:600;">= Stabile</span>'


def _render_group_matrix_table(
    group_key: str,
    group_title: str,
    prodotti: List[Dict[str, Any]],
    competitors: List[Dict[str, Any]],
    assoc_map: Dict[Tuple[str, str], Dict[str, Any]],
) -> str:
    """Costruisce la tabella HTML con lo scostamento medio % per Categoria o per Marchio."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in prodotti:
        g_name = (p.get(group_key) or "Non specificato").strip() or "Non specificato"
        groups.setdefault(g_name, []).append(p)

    if not groups or not competitors:
        return (
            f'<p style="color:#64748b;font-size:13px;margin:6px 0;">'
            f"Nessun dato disponibile per {_esc(group_title)}.</p>"
        )

    th_comps = "".join(
        f'<th style="padding:10px 12px;text-align:center;border-bottom:2px solid #e2e8f0;'
        f'color:#334155;font-size:12px;text-transform:uppercase;">{_esc(c.get("nome") or "Competitor")}</th>'
        for c in competitors
    )

    rows_html = []
    for g_name in sorted(groups.keys(), key=lambda x: x.lower()):
        g_prods = groups[g_name]
        td_Start = (
            f'<td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;'
            f'font-weight:600;color:#0f172a;font-size:13px;">{_esc(g_name)} '
            f'<span style="color:#94a3b8;font-weight:400;font-size:12px;">({len(g_prods)})</span></td>'
        )
        td_comps = []
        for c in competitors:
            c_id = str(c["id"])
            diffs: List[float] = []
            for p in g_prods:
                p_id = str(p["id"])
                rec = assoc_map.get((p_id, c_id))
                comp_p = rec.get("ultimo_prezzo") if rec else None
                d = _calc_diff_pct(p.get("mio_prezzo"), comp_p)
                if d is not None:
                    diffs.append(d)
            avg_d = (sum(diffs) / len(diffs)) if diffs else None
            td_comps.append(
                f'<td style="padding:10px 12px;text-align:center;border-bottom:1px solid #f1f5f9;">'
                f"{_render_diff_badge_inline(avg_d)}</td>"
            )
        rows_html.append(f"<tr>{td_Start}{''.join(td_comps)}</tr>")

    return f"""
    <div style="margin-top:14px;margin-bottom:18px;">
      <div style="font-size:14px;font-weight:700;color:#1e293b;margin-bottom:8px;">
        Scostamento Medio % per {_esc(group_title)}
      </div>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
        <thead>
          <tr style="background-color:#f8fafc;">
            <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;color:#334155;font-size:12px;text-transform:uppercase;">{_esc(group_title)}</th>
            {th_comps}
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </div>
    """


def genera_html_report() -> str:
    """
    Legge i dati attuali da Supabase (`impostazioni`, `prodotti`, `competitors`,
    `prodotti_competitor`, `seo_keywords`, `backlinks`) e genera un'Email HTML
    professionale, pulita e responsive (con CSS inline) strutturata in 3 blocchi:
    1. Prezzi & Competitor (Scostamento medio % per Categoria e Marchio + Top 5 prodotti più aggressivi)
    2. Posizionamento SEO (KPI Top 5, Top 10, >10 + Tabella variazioni migliori salite e scese)
    3. Stato Backlink (KPI Attivi vs Non Attivi + Alert rossi per link persi o diventati nofollow)
    """
    impostazioni = get_impostazioni(force_refresh=True)
    prodotti = get_prodotti()
    competitors = get_competitors_prezzi(only_active=True)
    associazioni = get_prodotti_competitor()
    seo_keywords = get_seo_keywords()
    backlinks = get_backlinks()

    nome_azienda = (impostazioni.get("nome_azienda") or "Mondoirrigazione SEO Suite").strip()
    sito_web = (impostazioni.get("sito_web") or "https://www.mondoirrigazione.it").strip()
    now_str = datetime.now().strftime("%d/%m/%Y alle %H:%M")

    # -------------------------------------------------------------------------
    # SEZIONE 1: PREZZI & COMPETITOR
    # -------------------------------------------------------------------------
    assoc_map: Dict[Tuple[str, str], Dict[str, Any]] = {
        (str(a["prodotto_id"]), str(a["competitor_id"])): a for a in associazioni
    }

    html_cat_table = _render_group_matrix_table(
        "categoria", "Categoria", prodotti, competitors, assoc_map
    )
    html_mar_table = _render_group_matrix_table(
        "marchio", "Marchio", prodotti, competitors, assoc_map
    )

    # Top 20 prodotti dove i competitor hanno il prezzo più aggressivo (diff % < 0)
    aggressive_offers: List[Dict[str, Any]] = []
    comp_by_id = {str(c["id"]): c for c in competitors}

    for p in prodotti:
        p_id = str(p["id"])
        mio_p = float(p.get("mio_prezzo") or 0.0)
        if mio_p <= 0:
            continue
        for c_id, c_obj in comp_by_id.items():
            rec = assoc_map.get((p_id, c_id))
            if not rec or rec.get("ultimo_prezzo") is None:
                continue
            comp_p = float(rec["ultimo_prezzo"] or 0.0)
            if comp_p <= 0:
                continue
            comp_prev_p = (
                float(rec["prezzo_precedente"])
                if rec.get("prezzo_precedente") is not None
                else None
            )
            diff_pct = _calc_diff_pct(mio_p, comp_p)
            if diff_pct is not None and diff_pct < 0:
                aggressive_offers.append(
                    {
                        "sku": p.get("sku") or "—",
                        "descrizione": p.get("descrizione") or "Prodotto",
                        "mio_prezzo": mio_p,
                        "comp_nome": c_obj.get("nome") or "Competitor",
                        "comp_prezzo": comp_p,
                        "comp_prev_prezzo": comp_prev_p,
                        "diff_pct": diff_pct,
                        "url_prodotto": (rec.get("url_prodotto") or "").strip(),
                    }
                )

    aggressive_offers.sort(key=lambda x: x["diff_pct"])
    top20_aggressive = aggressive_offers[:20]

    if top20_aggressive:
        agg_rows_html = []
        for item in top20_aggressive:
            link_html = (
                f'<a href="{_esc(item["url_prodotto"])}" style="color:#2563eb;text-decoration:none;font-weight:600;">Apri scheda &rarr;</a>'
                if item["url_prodotto"]
                else "—"
            )
            agg_rows_html.append(
                f"""
                <tr>
                  <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:13px;color:#0f172a;">
                    <strong>{_esc(item["descrizione"])}</strong><br>
                    <span style="color:#64748b;font-size:11px;">SKU: {_esc(item["sku"])}</span>
                  </td>
                  <td style="padding:10px 12px;text-align:right;border-bottom:1px solid #f1f5f9;font-size:13px;color:#334155;">
                    &euro; {item["mio_prezzo"]:.2f}
                  </td>
                  <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:13px;font-weight:600;color:#0f172a;">
                    {_esc(item["comp_nome"])}
                  </td>
                  <td style="padding:10px 12px;text-align:right;border-bottom:1px solid #f1f5f9;font-size:13px;font-weight:700;color:#b91c1c;">
                    &euro; {item["comp_prezzo"]:.2f}
                  </td>
                  <td style="padding:10px 12px;text-align:center;border-bottom:1px solid #f1f5f9;">
                    {_render_diff_badge_inline(item["diff_pct"])}
                  </td>
                  <td style="padding:10px 12px;text-align:center;border-bottom:1px solid #f1f5f9;">
                    {_render_evoluzione_badge_inline(item["comp_prezzo"], item["comp_prev_prezzo"])}
                  </td>
                  <td style="padding:10px 12px;text-align:center;border-bottom:1px solid #f1f5f9;font-size:12px;">
                    {link_html}
                  </td>
                </tr>
                """
            )
        html_top20_aggressive = f"""
        <div style="margin-top:16px;">
          <div style="font-size:14px;font-weight:700;color:#991b1b;margin-bottom:8px;">
            Top 20 Offerte Competitor Pi&ugrave; Aggressive (Prezzo Inferiore al Nostro)
          </div>
          <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#ffffff;border:1px solid #fecaca;border-radius:8px;overflow:hidden;">
            <thead>
              <tr style="background-color:#fef2f2;">
                <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #fecaca;color:#7f1d1d;font-size:12px;">Prodotto</th>
                <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #fecaca;color:#7f1d1d;font-size:12px;">Mio Prezzo</th>
                <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #fecaca;color:#7f1d1d;font-size:12px;">Competitor</th>
                <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #fecaca;color:#7f1d1d;font-size:12px;">Prezzo Comp.</th>
                <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #fecaca;color:#7f1d1d;font-size:12px;">Diff. %</th>
                <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #fecaca;color:#7f1d1d;font-size:12px;">Evoluzione (vs prec.)</th>
                <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #fecaca;color:#7f1d1d;font-size:12px;">Link</th>
              </tr>
            </thead>
            <tbody>
              {''.join(agg_rows_html)}
            </tbody>
          </table>
        </div>
        """
    else:
        html_top20_aggressive = """
        <div style="padding:12px 14px;background-color:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;color:#166534;font-size:13px;margin-top:12px;">
          Nessun competitor ha attualmente prezzi pi&ugrave; bassi dei nostri sui prodotti associati.
        </div>
        """

    # -------------------------------------------------------------------------
    # SEZIONE 2: POSIZIONAMENTO SEO
    # -------------------------------------------------------------------------
    kw_top5 = 0
    kw_top10 = 0
    kw_over10 = 0
    kw_out100 = 0

    kw_salite: List[Dict[str, Any]] = []
    kw_scese: List[Dict[str, Any]] = []
    kw_stabili: List[Dict[str, Any]] = []

    for kw in seo_keywords:
        pos_att_raw = kw.get("mia_posizione_attuale")
        pos_prec_raw = kw.get("mia_posizione_precedente")
        try:
            pos_att = int(pos_att_raw) if pos_att_raw is not None and int(pos_att_raw) > 0 else None
        except (ValueError, TypeError):
            pos_att = None
        try:
            pos_prec = int(pos_prec_raw) if pos_prec_raw is not None and int(pos_prec_raw) > 0 else None
        except (ValueError, TypeError):
            pos_prec = None

        if pos_att is not None:
            if pos_att <= 5:
                kw_top5 += 1
            elif pos_att <= 10:
                kw_top10 += 1
            else:
                kw_over10 += 1
        else:
            kw_out100 += 1

        delta = 0
        if pos_att is not None and pos_prec is not None:
            delta = pos_prec - pos_att
        elif pos_att is not None and pos_prec is None:
            delta = 0

        entry_kw = {
            "keyword": kw.get("keyword") or "",
            "volume": kw.get("volume_mensile"),
            "pos_att": pos_att,
            "pos_prec": pos_prec,
            "delta": delta,
        }
        if delta > 0:
            kw_salite.append(entry_kw)
        elif delta < 0:
            kw_scese.append(entry_kw)
        elif pos_att is not None:
            kw_stabili.append(entry_kw)

    kw_salite.sort(key=lambda x: (-x["delta"], x["pos_att"] or 999))
    kw_scese.sort(key=lambda x: (x["delta"], x["pos_att"] or 999))
    kw_stabili.sort(key=lambda x: (x["pos_att"] or 999))

    # Top 20 keyword migliori / più rilevanti (salite + scese + migliori posizionate fino a 20 elementi)
    seo_highlight_rows = (kw_salite[:10] + kw_scese[:10])[:20]
    if len(seo_highlight_rows) < 20:
        already_kw = {r["keyword"] for r in seo_highlight_rows}
        for st_kw in kw_stabili + kw_salite[10:] + kw_scese[10:]:
            if st_kw["keyword"] not in already_kw:
                seo_highlight_rows.append(st_kw)
                already_kw.add(st_kw["keyword"])
            if len(seo_highlight_rows) >= 20:
                break

    def _render_pos_pill(pos_val: Optional[int]) -> str:
        if pos_val is None:
            return '<span style="display:inline-block;padding:3px 8px;border-radius:12px;background:#fee2e2;color:#b91c1c;font-weight:700;font-size:12px;">&gt;100</span>'
        if pos_val <= 5:
            bg, fg = "#dcfce7", "#15803d"
        elif pos_val <= 10:
            bg, fg = "#fef3c7", "#b45309"
        else:
            bg, fg = "#fee2e2", "#b91c1c"
        return (
            f'<span style="display:inline-block;min-width:28px;padding:3px 8px;border-radius:14px;'
            f'background:{bg};color:{fg};font-weight:700;font-size:12px;text-align:center;">{pos_val}</span>'
        )

    def _render_delta_pill(delta_val: int) -> str:
        if delta_val > 0:
            return (
                f'<span style="display:inline-block;padding:2px 8px;border-radius:6px;'
                f'background:#dcfce7;color:#15803d;font-weight:700;font-size:12px;">'
                f"&#9650; +{delta_val}</span>"
            )
        if delta_val < 0:
            return (
                f'<span style="display:inline-block;padding:2px 8px;border-radius:6px;'
                f'background:#fee2e2;color:#b91c1c;font-weight:700;font-size:12px;">'
                f"&#9660; {delta_val}</span>"
            )
        return '<span style="color:#64748b;font-size:12px;font-weight:600;">= Stabile</span>'

    if seo_highlight_rows:
        seo_tr_list = []
        for k_item in seo_highlight_rows:
            vol_val = k_item["volume"]
            vol_str = f"{int(vol_val):,}".replace(",", ".") + " / mese" if vol_val else "—"
            prec_str = str(k_item["pos_prec"]) if k_item["pos_prec"] is not None else "—"
            seo_tr_list.append(
                f"""
                <tr>
                  <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:13px;font-weight:600;color:#0f172a;">
                    {_esc(k_item["keyword"])}
                  </td>
                  <td style="padding:10px 12px;text-align:right;border-bottom:1px solid #f1f5f9;font-size:12px;color:#475569;">
                    {vol_str}
                  </td>
                  <td style="padding:10px 12px;text-align:center;border-bottom:1px solid #f1f5f9;">
                    {_render_pos_pill(k_item["pos_att"])}
                  </td>
                  <td style="padding:10px 12px;text-align:center;border-bottom:1px solid #f1f5f9;font-size:12px;color:#64748b;">
                    {prec_str}
                  </td>
                  <td style="padding:10px 12px;text-align:center;border-bottom:1px solid #f1f5f9;">
                    {_render_delta_pill(k_item["delta"])}
                  </td>
                </tr>
                """
            )
        html_seo_table = f"""
        <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin-top:12px;">
          <thead>
            <tr style="background-color:#f8fafc;">
              <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e2e8f0;color:#334155;font-size:12px;text-transform:uppercase;">Keyword</th>
              <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #e2e8f0;color:#334155;font-size:12px;text-transform:uppercase;">Volume</th>
              <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #e2e8f0;color:#334155;font-size:12px;text-transform:uppercase;">Pos. Attuale</th>
              <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #e2e8f0;color:#334155;font-size:12px;text-transform:uppercase;">Pos. Prec.</th>
              <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #e2e8f0;color:#334155;font-size:12px;text-transform:uppercase;">Variazione</th>
            </tr>
          </thead>
          <tbody>
            {''.join(seo_tr_list)}
          </tbody>
        </table>
        """
    else:
        html_seo_table = '<p style="color:#64748b;font-size:13px;">Nessuna keyword SEO registrata.</p>'

    # -------------------------------------------------------------------------
    # SEZIONE 3: STATO BACKLINK & MENZIONI DEL BRAND
    # -------------------------------------------------------------------------
    tot_bl = len(backlinks)
    bl_attivi = sum(1 for b in backlinks if bool(b.get("stato_attivo")))
    bl_non_attivi = tot_bl - bl_attivi
    bl_menzioni = sum(
        1
        for b in backlinks
        if bool(b.get("stato_attivo"))
        and "menzione" in str(b.get("rel_attribute") or "").lower()
    )
    bl_link_attivi_puri = bl_attivi - bl_menzioni

    bl_alert_rows: List[Dict[str, Any]] = []
    bl_menzioni_rows: List[Dict[str, Any]] = []
    for b in backlinks:
        is_act = bool(b.get("stato_attivo"))
        rel_val = str(b.get("rel_attribute") or "").strip().lower()
        if is_act and "menzione" in rel_val:
            bl_menzioni_rows.append(
                {
                    "url_sorgente": (b.get("url_sorgente") or "").strip(),
                    "dominio_sorgente": clean_domain(b.get("url_sorgente") or "") or (b.get("url_sorgente") or ""),
                    "anchor_text": (b.get("anchor_text") or "").strip(),
                }
            )
            continue

        is_nofollow = is_act and rel_val in ("nofollow", "sponsored", "ugc")
        if not is_act or is_nofollow:
            bl_alert_rows.append(
                {
                    "url_sorgente": (b.get("url_sorgente") or "").strip(),
                    "dominio_sorgente": clean_domain(b.get("url_sorgente") or "") or (b.get("url_sorgente") or ""),
                    "url_destinazione": (b.get("url_destinazione") or "").strip(),
                    "http_status": b.get("http_status"),
                    "stato_attivo": is_act,
                    "rel_attribute": rel_val or "N/D",
                }
            )

    html_menzioni_block = ""
    if bl_menzioni_rows:
        m_tr = []
        for m_row in bl_menzioni_rows[:20]:
            m_tr.append(
                f"""
                <tr>
                  <td style="padding:9px 12px;border-bottom:1px solid #cffafe;font-size:13px;">
                    <a href="{_esc(m_row["url_sorgente"])}" style="color:#0e7490;font-weight:700;text-decoration:none;">
                      {_esc(m_row["dominio_sorgente"])}
                    </a><br>
                    <span style="color:#64748b;font-size:11px;">{_esc(m_row["url_sorgente"][:68])}</span>
                  </td>
                  <td style="padding:9px 12px;border-bottom:1px solid #cffafe;font-size:12px;color:#155e75;">
                    {_esc(m_row["anchor_text"] or "Menzione del brand rilevata")}
                  </td>
                  <td style="padding:9px 12px;text-align:center;border-bottom:1px solid #cffafe;">
                    <span style="display:inline-block;padding:3px 8px;border-radius:999px;background:#cffafe;color:#0e7490;font-weight:700;font-size:11px;">
                      Menzione (No Link)
                    </span>
                  </td>
                </tr>
                """
            )
        html_menzioni_block = f"""
        <div style="margin-top:14px;">
          <div style="font-size:14px;font-weight:700;color:#0e7490;margin-bottom:8px;">
            Menzioni Testuali del Brand Rilevate ({len(bl_menzioni_rows)})
          </div>
          <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;overflow:hidden;">
            <thead>
              <tr style="background-color:#cffafe;">
                <th style="padding:9px 12px;text-align:left;color:#155e75;font-size:12px;">Pagina Sorgente</th>
                <th style="padding:9px 12px;text-align:left;color:#155e75;font-size:12px;">Estratto Menzione</th>
                <th style="padding:9px 12px;text-align:center;color:#155e75;font-size:12px;">Tipo</th>
              </tr>
            </thead>
            <tbody>
              {''.join(m_tr)}
            </tbody>
          </table>
        </div>
        """

    if bl_alert_rows:
        bl_tr_list = []
        for b_al in bl_alert_rows:
            if not b_al["stato_attivo"]:
                badge_bl = (
                    f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;'
                    f'background:#fee2e2;color:#b91c1c;font-weight:700;font-size:12px;">'
                    f'NON ATTIVO / PERSO (HTTP {_esc(b_al["http_status"] or 0)})</span>'
                )
            else:
                badge_bl = (
                    f'<span style="display:inline-block;padding:3px 8px;border-radius:6px;'
                    f'background:#fef3c7;color:#b45309;font-weight:700;font-size:12px;">'
                    f'ATTIVO MA {_esc(b_al["rel_attribute"]).upper()}</span>'
                )
            bl_tr_list.append(
                f"""
                <tr>
                  <td style="padding:10px 12px;border-bottom:1px solid #fecaca;font-size:13px;">
                    <a href="{_esc(b_al["url_sorgente"])}" style="color:#1e293b;font-weight:700;text-decoration:none;">
                      {_esc(b_al["dominio_sorgente"])}
                    </a><br>
                    <span style="color:#64748b;font-size:11px;">{_esc(b_al["url_sorgente"][:65])}</span>
                  </td>
                  <td style="padding:10px 12px;border-bottom:1px solid #fecaca;font-size:12px;color:#475569;">
                    {_esc(b_al["url_destinazione"] or sito_web)}
                  </td>
                  <td style="padding:10px 12px;text-align:center;border-bottom:1px solid #fecaca;">
                    {badge_bl}
                  </td>
                </tr>
                """
            )
        html_bl_alerts = f"""
        <div style="margin-top:14px;">
          <div style="font-size:14px;font-weight:700;color:#991b1b;margin-bottom:8px;">
            Attenzione: Backlink Non Attivi / Persi o con Attributo Nofollow ({len(bl_alert_rows)})
          </div>
          <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#fff5f5;border:1px solid #fecaca;border-radius:8px;overflow:hidden;">
            <thead>
              <tr style="background-color:#fee2e2;">
                <th style="padding:10px 12px;text-align:left;color:#7f1d1d;font-size:12px;">Pagina Sorgente</th>
                <th style="padding:10px 12px;text-align:left;color:#7f1d1d;font-size:12px;">URL Destinazione</th>
                <th style="padding:10px 12px;text-align:center;color:#7f1d1d;font-size:12px;">Stato / Criticità</th>
              </tr>
            </thead>
            <tbody>
              {''.join(bl_tr_list)}
            </tbody>
          </table>
        </div>
        """
    else:
        html_bl_alerts = """
        <div style="padding:12px 14px;background-color:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;color:#166534;font-size:13px;margin-top:12px;">
          Tutti i backlink monitorati risultano <strong>Attivi</strong> e <strong>dofollow</strong>.
        </div>
        """

    # -------------------------------------------------------------------------
    # TEMPLATE EMAIL HTML COMPLETO
    # -------------------------------------------------------------------------
    html_doc = f"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Report Monitoraggio - {_esc(nome_azienda)}</title>
</head>
<body style="margin:0;padding:0;background-color:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
  <div style="max-width:820px;margin:24px auto;background-color:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(15,23,42,0.08);border:1px solid #e2e8f0;">

    <!-- HEADER -->
    <div style="background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 100%);padding:26px 32px;color:#ffffff;">
      <div style="font-size:12px;text-transform:uppercase;letter-spacing:1.2px;color:#93c5fd;font-weight:700;">
        Report Automatico SEO, Prezzi &amp; Backlink
      </div>
      <h1 style="margin:6px 0 4px 0;font-size:24px;font-weight:800;color:#ffffff;">
        {_esc(nome_azienda)}
      </h1>
      <div style="font-size:13px;color:#cbd5e1;">
        Dominio monitorato: <a href="{_esc(sito_web)}" style="color:#60a5fa;text-decoration:none;font-weight:600;">{_esc(sito_web)}</a>
        &nbsp;&bull;&nbsp; Generato il: <strong>{now_str}</strong>
      </div>
    </div>

    <div style="padding:28px 32px;">

      <!-- SEZIONE 1: PREZZI & COMPETITOR -->
      <div style="margin-bottom:32px;">
        <h2 style="margin:0 0 6px 0;font-size:18px;color:#0f172a;border-bottom:2px solid #e2e8f0;padding-bottom:8px;">
          1. Monitoraggio Prezzi &amp; Competitor ({len(prodotti)} prodotti)
        </h2>
        <p style="margin:6px 0 12px 0;font-size:13px;color:#475569;">
          Scostamento medio percentuale dei competitor rispetto ai nostri prezzi di listino
          (<span style="color:#15803d;font-weight:600;">Verde = competitor pi&ugrave; caro</span>,
          <span style="color:#b91c1c;font-weight:600;">Rosso = competitor pi&ugrave; economico</span>).
        </p>
        {html_cat_table}
        {html_mar_table}
        {html_top20_aggressive}
      </div>

      <!-- SEZIONE 2: POSIZIONAMENTO SEO -->
      <div style="margin-bottom:32px;">
        <h2 style="margin:0 0 12px 0;font-size:18px;color:#0f172a;border-bottom:2px solid #e2e8f0;padding-bottom:8px;">
          2. Posizionamento Organico SEO — Top 20 Keyword ({len(seo_keywords)} monitorate)
        </h2>
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:14px;">
          <tr>
            <td width="25%" style="padding:4px;">
              <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:11px;color:#166534;font-weight:700;text-transform:uppercase;">Top 5 (Pos. 1-5)</div>
                <div style="font-size:22px;font-weight:800;color:#15803d;margin-top:4px;">{kw_top5}</div>
              </div>
            </td>
            <td width="25%" style="padding:4px;">
              <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:11px;color:#92400e;font-weight:700;text-transform:uppercase;">Top 10 (Pos. 6-10)</div>
                <div style="font-size:22px;font-weight:800;color:#b45309;margin-top:4px;">{kw_top10}</div>
              </div>
            </td>
            <td width="25%" style="padding:4px;">
              <div style="background:#f8fafc;border:1px solid #cbd5e1;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:11px;color:#334155;font-weight:700;text-transform:uppercase;">Pos. 11 - 100</div>
                <div style="font-size:22px;font-weight:800;color:#0f172a;margin-top:4px;">{kw_over10}</div>
              </div>
            </td>
            <td width="25%" style="padding:4px;">
              <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:11px;color:#991b1b;font-weight:700;text-transform:uppercase;">Fuori Top 100</div>
                <div style="font-size:22px;font-weight:800;color:#b91c1c;margin-top:4px;">{kw_out100}</div>
              </div>
            </td>
          </tr>
        </table>
        {html_seo_table}
      </div>

      <!-- SEZIONE 3: STATO BACKLINK & MENZIONI -->
      <div style="margin-bottom:12px;">
        <h2 style="margin:0 0 12px 0;font-size:18px;color:#0f172a;border-bottom:2px solid #e2e8f0;padding-bottom:8px;">
          3. Monitoraggio Stato Backlink &amp; Menzioni ({tot_bl} totali)
        </h2>
        <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;">
          <tr>
            <td width="25%" style="padding:4px;">
              <div style="background:#f8fafc;border:1px solid #cbd5e1;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:11px;color:#475569;font-weight:700;text-transform:uppercase;">Totale Monitorati</div>
                <div style="font-size:22px;font-weight:800;color:#0f172a;margin-top:4px;">{tot_bl}</div>
              </div>
            </td>
            <td width="25%" style="padding:4px;">
              <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:11px;color:#166534;font-weight:700;text-transform:uppercase;">Link Attivi</div>
                <div style="font-size:22px;font-weight:800;color:#15803d;margin-top:4px;">{bl_link_attivi_puri}</div>
              </div>
            </td>
            <td width="25%" style="padding:4px;">
              <div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:11px;color:#0e7490;font-weight:700;text-transform:uppercase;">Menzioni (No Link)</div>
                <div style="font-size:22px;font-weight:800;color:#0891b2;margin-top:4px;">{bl_menzioni}</div>
              </div>
            </td>
            <td width="25%" style="padding:4px;">
              <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:11px;color:#991b1b;font-weight:700;text-transform:uppercase;">Non Attivi / Persi</div>
                <div style="font-size:22px;font-weight:800;color:#b91c1c;margin-top:4px;">{bl_non_attivi}</div>
              </div>
            </td>
          </tr>
        </table>
        {html_menzioni_block}
        {html_bl_alerts}
      </div>

    </div>

    <!-- FOOTER -->
    <div style="background-color:#f8fafc;border-top:1px solid #e2e8f0;padding:16px 32px;text-align:center;font-size:12px;color:#64748b;">
      Report generato automaticamente da <strong>{_esc(nome_azienda)}</strong> &bull; {now_str}
    </div>
  </div>
</body>
</html>"""

    return html_doc


# =========================================================================
# 3. INVIO EMAIL TRAMITE SMTP
# =========================================================================


def invia_email_report(html_content: Optional[str] = None) -> Tuple[bool, str]:
    """
    Legge le credenziali `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`,
    `email_destinatari` dalla tabella `impostazioni` (con fallback su eventuali variabili nel `.env`).
    Supporta più destinatari separati da virgola.
    Invia l'email usando `smtplib.SMTP` (con `starttls()` per porta 587 o `SMTP_SSL` per porta 465)
    e `MIMEMultipart("alternative")`.
    Restituisce `(True, "Email inviata con successo")` oppure `(False, messaggio_errore)`.
    """
    load_dotenv(override=True)
    impostazioni = get_impostazioni(force_refresh=True)

    smtp_host = (
        str(impostazioni.get("smtp_host") or "").strip()
        or os.getenv("SMTP_HOST", "").strip()
        or "smtp.gmail.com"
    )
    raw_port = (
        impostazioni.get("smtp_port")
        or os.getenv("SMTP_PORT", "").strip()
        or 587
    )
    try:
        smtp_port = int(raw_port)
    except (ValueError, TypeError):
        smtp_port = 587

    smtp_user = (
        str(impostazioni.get("smtp_user") or "").strip()
        or os.getenv("SMTP_USER", "").strip()
    )
    smtp_password = (
        str(impostazioni.get("smtp_password") or "").strip()
        or os.getenv("SMTP_PASSWORD", "").strip()
    )
    raw_destinatari = (
        str(impostazioni.get("email_destinatari") or "").strip()
        or os.getenv("EMAIL_DESTINATARI", "").strip()
    )

    destinatari = [
        addr.strip()
        for addr in re.split(r"[,;]+", raw_destinatari)
        if addr.strip() and "@" in addr.strip()
    ]

    if not smtp_user or not smtp_password:
        return (
            False,
            "Credenziali SMTP mancanti: compila 'Utente / Email SMTP' e 'Password SMTP / Password per le App' nelle Impostazioni.",
        )

    if not destinatari:
        return (
            False,
            "Nessun destinatario valido configurato: inserisci almeno un indirizzo in 'Email Destinatari Report'.",
        )

    if not html_content:
        html_content = genera_html_report()

    nome_azienda = (impostazioni.get("nome_azienda") or "SEO Suite").strip()
    data_oggi = datetime.now().strftime("%d/%m/%Y")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Report Prezzi, SEO & Backlink - {nome_azienda} ({data_oggi})"
    msg["From"] = f"{nome_azienda} <{smtp_user}>"
    msg["To"] = ", ".join(destinatari)

    plain_fallback = (
        f"Report Monitoraggio {nome_azienda} ({data_oggi}).\n"
        f"Visualizza questo messaggio con un client email compatibile con HTML."
    )
    msg.attach(MIMEText(plain_fallback, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20) as server:
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, destinatari, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, destinatari, msg.as_string())

        return (
            True,
            f"Email inviata con successo a: {', '.join(destinatari)}",
        )
    except Exception as exc:
        return (False, f"Errore SMTP durante l'invio dell'email: {exc}")

