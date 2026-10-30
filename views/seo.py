from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from db import (
    add_competitor_seo,
    bulk_upsert_seo_keywords,
    delete_competitor,
    delete_seo_keyword,
    get_competitors_seo,
    get_impostazioni,
    get_seo_keywords,
    get_seo_storico,
    get_ultime_posizioni_serp,
    read_csv_flexible,
    update_competitor,
    upsert_seo_keyword,
)
from scraper import clean_domain, esegui_aggiornamento_seo, unwrap_google_url

st.header(":material/monitoring: SEO & Posizionamento")
st.caption(
    "Monitoraggio posizionamento organico SERP (Top 100 Google.it), stima volumi di ricerca mensili e confronto con i competitor SEO."
)


# =========================================================================
# HELPER DI FORMATTAZIONE & RENDERING PALLINI COLORATI (POSIZIONE + VARIAZIONE)
# =========================================================================


def _format_volume(vol: Any) -> str:
    """Formatta il volume mensile con separatore delle migliaia (es. 1.200 / mese)."""
    if vol is None or pd.isna(vol):
        return "—"
    try:
        v = int(float(vol))
        if v <= 0:
            return "—"
        formatted = f"{v:,}".replace(",", ".")
        return f"{formatted} / mese"
    except (ValueError, TypeError):
        return "—"


def _parse_pos_int(pos: Any) -> Optional[int]:
    """Restituisce la posizione come intero 1..100 oppure None."""
    if pos is None or pd.isna(pos):
        return None
    try:
        p = int(float(pos))
        return p if 1 <= p <= 100 else None
    except (ValueError, TypeError):
        return None


def _render_position_circle_html(pos: Any, checked: bool = True) -> str:
    """
    Restituisce l'HTML di un cerchio colorato (border-radius: 50%, 34x34px, testo centrato bold bianco)
    per la posizione SERP:
    - Posizione da 1 a 5: Cerchio VERDE (#16a34a) con dentro il numero (es. 3).
    - Posizione da 6 a 10: Cerchio ARANCIO (#ea580c) con dentro il numero (es. 8).
    - Posizione da 11 in su (o non classificato >100 / None): Cerchio ROSSO (#dc2626) con dentro
      il numero (es. 18 oppure >100 / - se fuori dai primi 100).
    """
    p_int = _parse_pos_int(pos)

    if p_int is not None:
        if 1 <= p_int <= 5:
            bg_color = "#16a34a"  # Verde (Top 5)
            title_txt = f"Posizione #{p_int} (Top 5)"
        elif 6 <= p_int <= 10:
            bg_color = "#ea580c"  # Arancio (Top 6-10)
            title_txt = f"Posizione #{p_int} (Prima pagina: 6-10)"
        else:
            bg_color = "#dc2626"  # Rosso (11-100)
            title_txt = f"Posizione #{p_int} (Oltre la 10ª posizione)"

        text_val = str(p_int)
        font_sz = "0.75rem" if p_int == 100 else "0.84rem"
    else:
        bg_color = "#dc2626"  # Rosso (>100 o non classificato)
        text_val = ">100" if checked else "-"
        font_sz = "0.66rem" if text_val == ">100" else "0.84rem"
        title_txt = "Fuori dalla Top 100 (>100)" if checked else "Posizione non ancora rilevata"

    return (
        f'<div title="{title_txt}" style="display:inline-flex;align-items:center;'
        f"justify-content:center;width:34px;height:34px;border-radius:50%;"
        f"background-color:{bg_color};color:#ffffff;font-weight:700;"
        f'font-size:{font_sz};line-height:1;letter-spacing:-0.3px;box-shadow:0 1px 3px rgba(0,0,0,0.35);">'
        f"{text_val}</div>"
    )


def _render_pending_badge_html() -> str:
    """Badge HTML compatto quando la keyword non è ancora stata scansionata."""
    return (
        '<span style="display:inline-block;padding:4px 9px;border-radius:12px;'
        'background-color:rgba(148,163,184,0.16);color:#cbd5e1;font-size:0.76rem;font-weight:600;">'
        "In attesa di scansione</span>"
    )


def _compute_position_diff(kw_row: Dict[str, Any]) -> Optional[int]:
    """
    Calcola diff = mia_posizione_precedente - mia_posizione_attuale.
    Es. se ero 5° e ora sono 3°, diff = 5 - 3 = +2 (guadagnate 2 posizioni).
    """
    pos_att = _parse_pos_int(kw_row.get("mia_posizione_attuale"))
    pos_prec = _parse_pos_int(kw_row.get("mia_posizione_precedente"))
    has_check = bool(kw_row.get("ultimo_check"))

    if pos_att is not None and pos_prec is not None:
        return pos_prec - pos_att
    if pos_att is not None and pos_prec is None:
        return 0
    if pos_att is None and pos_prec is not None:
        return pos_prec - 101
    if has_check:
        return 0
    return None


def _render_variation_circle_html(diff: Optional[int]) -> str:
    """
    Restituisce l'HTML di un pallino circolare perfetto (border-radius: 50%, 34x34px,
    testo centrato bold bianco) per la Variazione:
    - VERDE (#16a34a) se diff >= 0 (posizionamento migliorato o invariato)
    - ARANCIO (#ea580c) se diff < 0 (posizionamento peggiorato)
    """
    if diff is None:
        return (
            '<div style="display:inline-flex;align-items:center;justify-content:center;'
            'width:34px;height:34px;border-radius:50%;background-color:#334155;'
            'color:#94a3b8;font-weight:700;font-size:0.82rem;line-height:1;">-</div>'
        )

    bg_color = "#16a34a" if diff >= 0 else "#ea580c"
    text_val = f"+{diff}" if diff > 0 else str(diff)
    title_txt = (
        f"Migliorato di {diff} posizioni"
        if diff > 0
        else ("Posizione stabile" if diff == 0 else f"Peggiorato di {abs(diff)} posizioni")
    )

    return (
        f'<div title="{title_txt}" style="display:inline-flex;align-items:center;'
        f"justify-content:center;width:34px;height:34px;border-radius:50%;"
        f"background-color:{bg_color};color:#ffffff;font-weight:700;"
        f'font-size:0.82rem;line-height:1;box-shadow:0 1px 3px rgba(0,0,0,0.35);">'
        f"{text_val}</div>"
    )


def _format_short_dt(ts_val: Any) -> str:
    """Formatta un timestamp ISO in GG/MM HH:MM per l'asse X del grafico storico."""
    if not ts_val:
        return "Rilevazione"
    try:
        cleaned = str(ts_val).replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return str(ts_val)[:16]


@st.dialog("Conferma Eliminazione Keyword")
def dialog_elimina_keyword(kw_row: Dict[str, Any]) -> None:
    kw_id = str(kw_row["id"])
    kw_name = kw_row.get("keyword") or ""
    st.warning(
        f"Sei sicuro di voler eliminare la keyword **{kw_name}** e tutto il suo storico SERP?",
        icon=":material/warning:",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            "Elimina definitivamente",
            type="primary",
            icon=":material/delete:",
            use_container_width=True,
            key=f"confirm_del_kw_{kw_id}",
        ):
            try:
                delete_seo_keyword(kw_id)
                if st.session_state.get("selected_seo_kw") == kw_id:
                    st.session_state.pop("selected_seo_kw", None)
                st.toast("Keyword eliminata!", icon=":material/check_circle:")
                st.rerun()
            except Exception as exc:
                st.error(f"Errore durante l'eliminazione: {exc}", icon=":material/error:")
    with c2:
        if st.button("Annulla", use_container_width=True, key=f"cancel_del_kw_{kw_id}"):
            st.rerun()


# =========================================================================
# CARICAMENTO DATI DA SUPABASE
# =========================================================================

impostazioni = get_impostazioni()
mio_sito_nome = impostazioni.get("nome_azienda") or "Nostro Sito"
mio_sito_dom = clean_domain(impostazioni.get("sito_web") or "")

keywords = get_seo_keywords()
competitors_seo_attivi = get_competitors_seo(only_active=True)
all_competitors = get_competitors_seo(only_active=False)

# Lettura completa di seo_storico in memoria per popolare sia la mappa ultima rilevazione
# sia i grafici inline senza dover fare query ripetute per ogni riga
all_storico_rows = get_seo_storico()
storico_by_kw: Dict[str, List[Dict[str, Any]]] = {}
latest_serp_map: Dict[str, Dict[str, Any]] = {}

for r in all_storico_rows:
    kid = str(r.get("keyword_id") or "")
    if not kid:
        continue
    storico_by_kw.setdefault(kid, []).append(r)
    if kid not in latest_serp_map:
        latest_serp_map[kid] = {"mio": {}, "competitors": {}}

    ts = r.get("data_rilevazione") or r.get("created_at")
    entry = {
        "posizione": r.get("posizione"),
        "url_posizionato": r.get("url_posizionato"),
        "data": ts,
    }
    if r.get("is_mio_sito"):
        latest_serp_map[kid]["mio"] = entry
    elif r.get("competitor_id"):
        latest_serp_map[kid]["competitors"][str(r["competitor_id"])] = entry

tab_report, tab_keywords, tab_comp_seo = st.tabs(
    [
        ":material/monitoring: Report Posizionamento",
        ":material/key: Gestione Keyword",
        ":material/domain: Gestione Competitor SEO",
    ]
)

# -------------------------------------------------------------------------
# TAB 1: REPORT POSIZIONAMENTO (SCHERMATA INIZIALE)
# -------------------------------------------------------------------------
with tab_report:
    # Box Azione Scansione SERP + KPI in evidenza in cima al Tab 1
    with st.container(border=True):
        head_l, head_r = st.columns([1.6, 1.8], vertical_alignment="center")
        with head_l:
            st.markdown("#### :material/travel_explore: Scansione SERP Google.it & Volumi AI")
            st.caption(
                f"Dominio monitorato: **`{mio_sito_dom or 'Non configurato'}`** • "
                f"Competitor SEO attivi: **{len(competitors_seo_attivi)}** • "
                f"Keyword a catalogo: **{len(keywords)}**"
            )
        with head_r:
            b_col1, b_col2 = st.columns(2)
            with b_col1:
                run_all_seo = st.button(
                    "Aggiorna Posizionamenti e Volumi SEO",
                    icon=":material/sync:",
                    type="primary",
                    use_container_width=True,
                    key="btn_run_all_seo_top",
                )
            with b_col2:
                run_missing_seo = st.button(
                    "Scansiona solo Keyword senza posizione",
                    icon=":material/bolt:",
                    type="secondary",
                    use_container_width=True,
                    key="btn_run_missing_seo_top",
                )

        if run_all_seo or run_missing_seo:
            if not keywords:
                st.warning(
                    "Aggiungi almeno una keyword nel tab 'Gestione Keyword' prima di avviare la scansione.",
                    icon=":material/warning:",
                )
            else:
                only_missing_flag = bool(run_missing_seo)
                seo_prog = st.progress(
                    0.0,
                    text=(
                        "Avvio scansione SERP per le keyword senza posizione..."
                        if only_missing_flag
                        else "Avvio analisi SERP Top 100 e stima volumi per tutte le keyword..."
                    ),
                )

                def _update_seo_prog(pct: float, msg: str) -> None:
                    seo_prog.progress(pct, text=msg)

                try:
                    seo_stats = esegui_aggiornamento_seo(
                        progress_callback=_update_seo_prog,
                        only_missing_position=only_missing_flag,
                    )
                    seo_prog.empty()
                    st.session_state["last_seo_scan_log"] = seo_stats.get("logs", [])
                    st.toast(
                        f"Scansione completata! ({seo_stats.get('keywords_analizzate', 0)} keyword aggiornate)",
                        icon=":material/check_circle:",
                    )
                    st.rerun()
                except Exception as exc:
                    seo_prog.empty()
                    st.error(
                        f"Errore durante l'aggiornamento SEO: {exc}",
                        icon=":material/error:",
                    )

    # KPI sintetici
    tot_kw = len(keywords)
    in_top5 = sum(
        1
        for k in keywords
        if _parse_pos_int(k.get("mia_posizione_attuale")) is not None
        and 1 <= (_parse_pos_int(k.get("mia_posizione_attuale")) or 999) <= 5
    )
    in_top10 = sum(
        1
        for k in keywords
        if _parse_pos_int(k.get("mia_posizione_attuale")) is not None
        and 1 <= (_parse_pos_int(k.get("mia_posizione_attuale")) or 999) <= 10
    )
    in_top100 = sum(
        1
        for k in keywords
        if _parse_pos_int(k.get("mia_posizione_attuale")) is not None
    )
    kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)
    kpi_c1.metric("Keyword Monitorate", tot_kw)
    kpi_c2.metric("In Top 5 (Fascia Verde)", in_top5)
    kpi_c3.metric("In Top 10 (1ª Pagina)", in_top10)
    kpi_c4.metric("In Top 100", in_top100)

    if st.session_state.get("last_seo_scan_log"):
        with st.expander(":material/receipt_long: Risultati ultima scansione SERP", expanded=False):
            for line in st.session_state["last_seo_scan_log"]:
                st.markdown(f"- {line}")
            if st.button(
                "Chiudi log SERP",
                icon=":material/close:",
                key="btn_clear_seo_log",
            ):
                st.session_state.pop("last_seo_scan_log", None)
                st.rerun()

    st.divider()

    if not keywords:
        st.info(
            "Nessuna keyword configurata. Vai nel tab **Gestione Keyword** per aggiungere o importare da CSV le tue parole chiave.",
            icon=":material/info:",
        )
    else:
        st.subheader(f":material/table_chart: Posizionamento Organico ({len(keywords)} Keyword)")
        st.caption(
            "Fasce posizione: **Verde** (1–5) • **Arancio** (6–10) • **Rosso** (11+ o >100). "
            "Clicca su una keyword o sul pulsante **Dettaglio** per aprire subito sotto la riga il confronto con i competitor e il grafico storico."
        )

        h_cols = st.columns([3.2, 1.8, 1.8, 1.2, 1.3], vertical_alignment="center")
        h_cols[0].caption("**Keyword**")
        h_cols[1].caption("**Ricerche Mensili (Stima)**")
        h_cols[2].caption("**Mia Posizione Attuale**")
        h_cols[3].caption("**Variazione**")
        h_cols[4].caption("**Analisi SERP**")

        selected_kw_id = st.session_state.get("selected_seo_kw")
        comp_name_map: Dict[str, str] = {
            str(c["id"]): (c.get("nome") or clean_domain(c.get("dominio") or "Competitor"))
            for c in all_competitors
        }

        for kw in keywords:
            kw_id = str(kw["id"])
            kw_text = kw.get("keyword") or ""
            vol_str = _format_volume(kw.get("volume_mensile"))
            kw_storico_rows = storico_by_kw.get(kw_id, [])
            has_check = bool(kw.get("ultimo_check")) or bool(kw_storico_rows)

            mio_serp_info = latest_serp_map.get(kw_id, {}).get("mio", {})
            mio_url_pos = (mio_serp_info.get("url_posizionato") or "").strip()

            pos_circle_html = _render_position_circle_html(
                kw.get("mia_posizione_attuale"),
                checked=has_check,
            )
            diff_val = _compute_position_diff(kw)
            var_circle_html = _render_variation_circle_html(diff_val)

            is_selected = str(selected_kw_id) == kw_id

            # Contenitore unico della riga: quando selezionata mostra IMMEDIATAMENTE SOTTO il Dettaglio Inline!
            with st.container(border=True):
                r_cols = st.columns([3.2, 1.8, 1.8, 1.2, 1.3], vertical_alignment="center")

                # 1. Keyword (cliccabile direttamente)
                with r_cols[0]:
                    if st.button(
                        kw_text,
                        icon=":material/keyboard_arrow_up:" if is_selected else ":material/keyboard_arrow_down:",
                        type="primary" if is_selected else "tertiary",
                        key=f"btn_kw_name_{kw_id}",
                        use_container_width=True,
                    ):
                        if is_selected:
                            st.session_state.pop("selected_seo_kw", None)
                        else:
                            st.session_state["selected_seo_kw"] = kw_id
                        st.rerun()

                # 2. Ricerche Mensili (Stima)
                with r_cols[1]:
                    st.markdown(f"**{vol_str}**")

                # 3. Mia Posizione Attuale (Cerchio colorato HTML/CSS + icona link cliccabile accanto)
                with r_cols[2]:
                    p_col_circle, p_col_link = st.columns([1, 2.2], vertical_alignment="center")
                    with p_col_circle:
                        st.markdown(pos_circle_html, unsafe_allow_html=True)
                    with p_col_link:
                        if mio_url_pos:
                            st.markdown(f"[:material/open_in_new:]({mio_url_pos})")

                # 4. Variazione (Pallino Colorato HTML/CSS)
                with r_cols[3]:
                    st.markdown(var_circle_html, unsafe_allow_html=True)

                # 5. Pulsante Dettaglio / Chiudi
                with r_cols[4]:
                    if st.button(
                        "Chiudi" if is_selected else "Dettaglio",
                        icon=":material/close:" if is_selected else ":material/insights:",
                        type="primary" if is_selected else "secondary",
                        key=f"btn_kw_detail_{kw_id}",
                        use_container_width=True,
                    ):
                        if is_selected:
                            st.session_state.pop("selected_seo_kw", None)
                        else:
                            st.session_state["selected_seo_kw"] = kw_id
                        st.rerun()

                # =============================================================
                # PANNELLO DETTAGLIO INLINE (SUBITO SOTTO LA RIGA SELEZIONATA)
                # =============================================================
                if is_selected:
                    st.divider()

                    sub_head_l, sub_head_r = st.columns([3.2, 1.4], vertical_alignment="center")
                    with sub_head_l:
                        st.markdown(
                            f"##### :material/insights: Dettaglio SERP & Storico: **{kw_text}**"
                        )
                    with sub_head_r:
                        if st.button(
                            "Scansiona ora questa keyword",
                            icon=":material/bolt:",
                            type="primary" if not has_check else "secondary",
                            use_container_width=True,
                            key=f"btn_scan_single_kw_{kw_id}",
                        ):
                            single_prog = st.progress(
                                0.1, text=f"Scansione SERP in corso per '{kw_text}'..."
                            )
                            try:
                                esegui_aggiornamento_seo(
                                    progress_callback=lambda p, m: single_prog.progress(p, text=m),
                                    target_keyword_id=kw_id,
                                )
                                single_prog.empty()
                                st.toast(
                                    f"Keyword '{kw_text}' scansionata con successo!",
                                    icon=":material/check_circle:",
                                )
                                st.rerun()
                            except Exception as exc:
                                single_prog.empty()
                                st.error(f"Errore scansione keyword: {exc}", icon=":material/error:")

                    col_det_left, col_det_right = st.columns([1.05, 1.35], gap="large")

                    # ---------------------------------------------------------
                    # COLONNA SINISTRA: POSIZIONAMENTO ATTUALE (IO VS COMPETITOR)
                    # ---------------------------------------------------------
                    with col_det_left:
                        st.markdown("**:material/compare_arrows: Posizionamento Attuale (Io vs Competitor)**")

                        kw_serp_entry = latest_serp_map.get(kw_id, {"mio": {}, "competitors": {}})
                        mio_latest = kw_serp_entry.get("mio", {})
                        comp_latest_map = kw_serp_entry.get("competitors", {})

                        mia_pos_val = kw.get("mia_posizione_attuale")
                        if mia_pos_val is None and mio_latest.get("posizione") is not None:
                            mia_pos_val = mio_latest.get("posizione")
                        mio_url_val = unwrap_google_url(
                            (mio_latest.get("url_posizionato") or "").strip()
                        )

                        comparison_rows: List[Dict[str, Any]] = [
                            {
                                "is_mio": True,
                                "nome": f"{mio_sito_nome} (Nostro Sito)",
                                "dominio": mio_sito_dom or "—",
                                "posizione": mia_pos_val,
                                "url": mio_url_val,
                            }
                        ]

                        for comp in competitors_seo_attivi:
                            c_id = str(comp["id"])
                            c_nome = comp.get("nome") or "Competitor"
                            c_dom = clean_domain(comp.get("dominio") or "")
                            c_info = comp_latest_map.get(c_id, {})
                            comparison_rows.append(
                                {
                                    "is_mio": False,
                                    "nome": c_nome,
                                    "dominio": c_dom or "—",
                                    "posizione": c_info.get("posizione"),
                                    "url": unwrap_google_url(
                                        (c_info.get("url_posizionato") or "").strip()
                                    ),
                                }
                            )

                        for c_row in comparison_rows:
                            with st.container(border=True):
                                rc1, rc2, rc3 = st.columns([2.2, 1.1, 1.3], vertical_alignment="center")
                                with rc1:
                                    title_prefix = ":material/star: " if c_row["is_mio"] else ""
                                    st.markdown(
                                        f"{title_prefix}**{c_row['nome']}**<br>"
                                        f"<span style='color:#94a3b8;font-size:0.8rem;'><code>{c_row['dominio']}</code></span>",
                                        unsafe_allow_html=True,
                                    )
                                with rc2:
                                    if not has_check:
                                        st.markdown(
                                            _render_pending_badge_html(),
                                            unsafe_allow_html=True,
                                        )
                                    else:
                                        st.markdown(
                                            _render_position_circle_html(
                                                c_row["posizione"],
                                                checked=True,
                                            ),
                                            unsafe_allow_html=True,
                                        )
                                with rc3:
                                    if c_row["url"]:
                                        st.markdown(
                                            f"[:material/open_in_new: Apri URL]({c_row['url']})"
                                        )
                                    elif not has_check:
                                        st.caption("Da rilevare")
                                    else:
                                        st.caption("Fuori Top 100")

                    # ---------------------------------------------------------
                    # COLONNA DESTRA: GRAFICO STORICO POSIZIONAMENTI (PLOTLY)
                    # ---------------------------------------------------------
                    with col_det_right:
                        st.markdown("**:material/show_chart: Grafico Storico Posizionamenti**")

                        storico_kw = list(kw_storico_rows)
                        if not storico_kw and has_check:
                            fallback_ts = kw.get("ultimo_check") or datetime.now().isoformat()
                            storico_kw = [
                                {
                                    "keyword_id": kw_id,
                                    "competitor_id": None,
                                    "is_mio_sito": True,
                                    "posizione": mia_pos_val,
                                    "url_posizionato": mio_url_val,
                                    "data_rilevazione": fallback_ts,
                                }
                            ]
                            for comp in competitors_seo_attivi:
                                cid = str(comp["id"])
                                c_info = comp_latest_map.get(cid, {})
                                storico_kw.append(
                                    {
                                        "keyword_id": kw_id,
                                        "competitor_id": cid,
                                        "is_mio_sito": False,
                                        "posizione": c_info.get("posizione"),
                                        "url_posizionato": c_info.get("url_posizionato"),
                                        "data_rilevazione": fallback_ts,
                                    }
                                )

                        if not storico_kw:
                            st.info(
                                "Questa keyword è in attesa della prima scansione SERP. "
                                "Clicca su **Scansiona ora questa keyword** qui sopra oppure su **Aggiorna Posizionamenti e Volumi SEO** per popolare il grafico.",
                                icon=":material/info:",
                            )
                        else:
                            series_map: Dict[str, Dict[str, Any]] = {
                                "__mio__": {
                                    "label": f"{mio_sito_nome} (Nostro Sito)",
                                    "is_mio": True,
                                    "x": [],
                                    "y": [],
                                    "hover": [],
                                }
                            }

                            for comp in competitors_seo_attivi:
                                cid = str(comp["id"])
                                series_map[cid] = {
                                    "label": comp_name_map.get(cid, "Competitor"),
                                    "is_mio": False,
                                    "x": [],
                                    "y": [],
                                    "hover": [],
                                }

                            for rec in storico_kw:
                                ts_raw = rec.get("data_rilevazione") or rec.get("created_at")
                                dt_label = _format_short_dt(ts_raw)
                                pos_num = _parse_pos_int(rec.get("posizione"))

                                plot_y = pos_num if pos_num is not None else 100
                                hover_pos = f"#{pos_num}" if pos_num is not None else "> 100"

                                key_series = (
                                    "__mio__"
                                    if rec.get("is_mio_sito")
                                    else str(rec.get("competitor_id") or "")
                                )
                                if key_series not in series_map and key_series:
                                    series_map[key_series] = {
                                        "label": comp_name_map.get(key_series, "Competitor"),
                                        "is_mio": False,
                                        "x": [],
                                        "y": [],
                                        "hover": [],
                                    }

                                if key_series in series_map:
                                    series_map[key_series]["x"].append(dt_label)
                                    series_map[key_series]["y"].append(plot_y)
                                    series_map[key_series]["hover"].append(hover_pos)

                            max_points = max(
                                (len(s["x"]) for s in series_map.values()),
                                default=0,
                            )
                            if max_points <= 1:
                                st.caption(
                                    ":material/info: È presente una sola rilevazione iniziale (mostrata con i pallini sul grafico). "
                                    "La linea temporale si formerà a partire dalla seconda rilevazione."
                                )

                            palette = [
                                "#f59e0b",
                                "#38bdf8",
                                "#ec4899",
                                "#a855f7",
                                "#14b8a6",
                                "#f97316",
                                "#eab308",
                            ]

                            fig = go.Figure()
                            comp_color_idx = 0

                            for s_key, s_data in series_map.items():
                                if not s_data["x"]:
                                    continue
                                is_mio = s_data["is_mio"]
                                line_color = (
                                    "#22c55e"
                                    if is_mio
                                    else palette[comp_color_idx % len(palette)]
                                )
                                if not is_mio:
                                    comp_color_idx += 1

                                fig.add_trace(
                                    go.Scatter(
                                        x=s_data["x"],
                                        y=s_data["y"],
                                        customdata=s_data["hover"],
                                        mode="lines+markers",
                                        name=s_data["label"],
                                        line=dict(
                                            color=line_color,
                                            width=3.5 if is_mio else 2.2,
                                        ),
                                        marker=dict(
                                            size=10,
                                            symbol="circle",
                                            line=dict(width=1.5, color="#0f172a"),
                                        ),
                                        hovertemplate=(
                                            f"<b>{s_data['label']}</b><br>"
                                            "Data: %{x}<br>"
                                            "Posizione: <b>%{customdata}</b><extra></extra>"
                                        ),
                                    )
                                )

                            fig.update_layout(
                                template="plotly_dark",
                                height=380,
                                margin=dict(l=15, r=15, t=25, b=15),
                                xaxis=dict(title="Data Rilevazione"),
                                yaxis=dict(
                                    title="Posizione Google (SERP)",
                                    autorange="reversed",
                                    minallowed=1,
                                    dtick=10,
                                ),
                                legend=dict(
                                    orientation="h",
                                    yanchor="bottom",
                                    y=1.02,
                                    xanchor="left",
                                    x=0,
                                ),
                                hovermode="x unified",
                            )

                            st.plotly_chart(
                                fig,
                                use_container_width=True,
                                key=f"plotly_seo_hist_{kw_id}",
                            )

                    # ---------------------------------------------------------
                    # SEZIONE TOP 5 ATTUALE IN SERP (GOOGLE.IT)
                    # ---------------------------------------------------------
                    st.divider()
                    st.markdown("**:material/leaderboard: Top 5 Attuale in SERP (Google.it)**")

                    raw_top5 = kw.get("top_5_serp")
                    top5_list: List[Dict[str, Any]] = []
                    if isinstance(raw_top5, list):
                        top5_list = [x for x in raw_top5 if isinstance(x, dict)]
                    elif isinstance(raw_top5, str) and raw_top5.strip():
                        import json as _json
                        try:
                            parsed_t5 = _json.loads(raw_top5)
                            if isinstance(parsed_t5, list):
                                top5_list = [x for x in parsed_t5 if isinstance(x, dict)]
                        except Exception:
                            top5_list = []

                    if not top5_list:
                        st.caption(
                            "I primi 5 risultati organici della SERP verranno salvati e mostrati qui al prossimo click su **Scansiona ora questa keyword** o **Aggiorna Posizionamenti e Volumi SEO**."
                        )
                    else:
                        comp_domains_set = {
                            clean_domain(c.get("dominio") or "")
                            for c in all_competitors
                            if clean_domain(c.get("dominio") or "")
                        }

                        for idx_t5, t5_item in enumerate(top5_list[:5], start=1):
                            t5_pos = int(t5_item.get("pos") or idx_t5)
                            raw_t5_link = (t5_item.get("link") or "").strip()
                            t5_link = unwrap_google_url(raw_t5_link)
                            raw_t5_dom = (t5_item.get("domain") or "").strip()
                            if not raw_t5_dom or raw_t5_dom.startswith("google."):
                                t5_dom = clean_domain(t5_link)
                            else:
                                t5_dom = raw_t5_dom
                            t5_title = (t5_item.get("title") or t5_link or "Pagina").strip()

                            is_our_dom = bool(
                                mio_sito_dom
                                and t5_dom
                                and (
                                    t5_dom == mio_sito_dom
                                    or t5_dom.endswith("." + mio_sito_dom)
                                )
                            )
                            is_comp_dom = bool(
                                not is_our_dom
                                and t5_dom
                                and any(
                                    t5_dom == cd or t5_dom.endswith("." + cd)
                                    for cd in comp_domains_set
                                )
                            )

                            role_badge_html = ""
                            if is_our_dom:
                                role_badge_html = (
                                    '&nbsp;<span style="display:inline-block;padding:2px 8px;'
                                    "border-radius:10px;background-color:#16a34a;color:#ffffff;"
                                    'font-size:0.72rem;font-weight:700;">Nostro Sito</span>'
                                )
                            elif is_comp_dom:
                                role_badge_html = (
                                    '&nbsp;<span style="display:inline-block;padding:2px 8px;'
                                    "border-radius:10px;background-color:#2563eb;color:#ffffff;"
                                    'font-size:0.72rem;font-weight:700;">Competitor</span>'
                                )

                            with st.container(border=True):
                                t5_c1, t5_c2, t5_c3, t5_c4 = st.columns(
                                    [0.6, 2.2, 4.2, 0.7], vertical_alignment="center"
                                )
                                with t5_c1:
                                    st.markdown(
                                        _render_position_circle_html(t5_pos, checked=True),
                                        unsafe_allow_html=True,
                                    )
                                with t5_c2:
                                    st.markdown(
                                        f"**{t5_dom or '—'}**{role_badge_html}",
                                        unsafe_allow_html=True,
                                    )
                                with t5_c3:
                                    st.markdown(t5_title)
                                with t5_c4:
                                    if t5_link:
                                        st.markdown(f"[:material/open_in_new:]({t5_link})")


# -------------------------------------------------------------------------
# TAB 2: GESTIONE KEYWORD
# -------------------------------------------------------------------------
with tab_keywords:
    col_add_kw, col_csv_kw = st.columns(2)

    # 1. Aggiungi Keyword Manuale
    with col_add_kw:
        with st.container(border=True):
            st.markdown("#### :material/add_circle: Aggiungi Keyword Manuale")
            st.caption(
                "Se lasci il volume mensile a `0`, verrà stimato automaticamente dall'AI al primo aggiornamento."
            )
            with st.form("form_add_seo_keyword", clear_on_submit=True):
                new_kw = st.text_input(
                    "Keyword *",
                    placeholder="Es. centralina irrigazione 6 zone",
                )
                new_vol = st.number_input(
                    "Volume Mensile (Opzionale - 0 = Stima AI)",
                    min_value=0,
                    step=10,
                    value=0,
                )
                sub_kw = st.form_submit_button(
                    "Salva Keyword",
                    type="primary",
                    icon=":material/save:",
                    use_container_width=True,
                )
                if sub_kw:
                    if not new_kw.strip():
                        st.warning(
                            "Inserisci una keyword valida.",
                            icon=":material/warning:",
                        )
                    else:
                        try:
                            upsert_seo_keyword(
                                keyword=new_kw.strip(),
                                volume_mensile=int(new_vol) if new_vol > 0 else None,
                            )
                            st.toast(
                                f"Keyword '{new_kw.strip()}' salvata!",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(
                                f"Errore durante il salvataggio: {exc}",
                                icon=":material/error:",
                            )

    # 2. Carica Keyword da CSV + Template (supporta sia ',' sia ';')
    with col_csv_kw:
        with st.container(border=True):
            st.markdown("#### :material/upload_file: Carica Keyword da CSV")
            st.caption(
                "Supporta sia separatore virgola (`,`) che punto e virgola (`;`). "
                "Colonne: `keyword` (obbligatoria), `volume_mensile` (opzionale)."
            )
            template_csv_content = (
                "keyword,volume_mensile\n"
                "centralina irrigazione 6 stazioni,480\n"
                "programmatore irrigazione wifi,\n"
                "elettrovalvola 24v irrigazione,720\n"
            )
            st.download_button(
                label="Scarica template CSV Keyword",
                data=template_csv_content.encode("utf-8"),
                file_name="template_seo_keywords.csv",
                mime="text/csv",
                icon=":material/download:",
                use_container_width=True,
            )

            uploaded_kw_csv = st.file_uploader(
                "Seleziona file CSV Keyword",
                type=["csv"],
                key="uploader_seo_kw_csv",
            )
            if uploaded_kw_csv is not None:
                if st.button(
                    "Importa Keyword dal CSV",
                    type="primary",
                    icon=":material/cloud_upload:",
                    use_container_width=True,
                    key="btn_import_seo_kw_csv",
                ):
                    try:
                        df_kw = read_csv_flexible(uploaded_kw_csv)
                        if "keyword" not in df_kw.columns:
                            st.error(
                                "Il file CSV deve contenere la colonna obbligatoria `keyword`.",
                                icon=":material/error:",
                            )
                        else:
                            records_to_import: List[Dict[str, Any]] = []
                            for _, r in df_kw.iterrows():
                                kw_val = r.get("keyword")
                                if kw_val is None or pd.isna(kw_val) or not str(kw_val).strip():
                                    continue
                                kw_clean = str(kw_val).strip().rstrip(";,").strip()
                                if not kw_clean:
                                    continue
                                raw_vol = (
                                    r.get("volume_mensile")
                                    if "volume_mensile" in df_kw.columns
                                    else None
                                )
                                clean_vol = (
                                    None
                                    if raw_vol is None or pd.isna(raw_vol) or not str(raw_vol).strip()
                                    else str(raw_vol).strip()
                                )
                                records_to_import.append(
                                    {
                                        "keyword": kw_clean,
                                        "volume_mensile": clean_vol,
                                    }
                                )
                            imported_n = bulk_upsert_seo_keywords(records_to_import)
                            st.toast(
                                f"Importate/aggiornate {imported_n} keyword!",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                    except Exception as exc:
                        st.error(
                            f"Errore durante la lettura del CSV: {exc}",
                            icon=":material/error:",
                        )

    st.divider()

    # 3. Elenco Keyword
    st.subheader(f":material/list_alt: Elenco Keyword ({len(keywords)})")

    if not keywords:
        st.info("Nessuna keyword presente a database.", icon=":material/info:")
    else:
        kh = st.columns([3.5, 1.8, 1.8, 1.2], vertical_alignment="center")
        kh[0].caption("**Keyword**")
        kh[1].caption("**Volume Mensile**")
        kh[2].caption("**Posizione Attuale**")
        kh[3].caption("**Elimina**")

        for kw in keywords:
            kw_id = str(kw["id"])
            has_check = bool(kw.get("ultimo_check")) or bool(storico_by_kw.get(kw_id))
            with st.container(border=True):
                kc = st.columns([3.5, 1.8, 1.8, 1.2], vertical_alignment="center")
                kc[0].markdown(f"**{kw.get('keyword', '')}**")
                kc[1].write(_format_volume(kw.get("volume_mensile")))
                kc[2].markdown(
                    _render_position_circle_html(
                        kw.get("mia_posizione_attuale"),
                        checked=has_check,
                    ),
                    unsafe_allow_html=True,
                )
                with kc[3]:
                    if st.button(
                        "Elimina",
                        icon=":material/delete:",
                        key=f"btn_del_kw_tab2_{kw_id}",
                        use_container_width=True,
                    ):
                        dialog_elimina_keyword(kw)


# -------------------------------------------------------------------------
# TAB 3: GESTIONE COMPETITOR SEO
# -------------------------------------------------------------------------
with tab_comp_seo:
    with st.container(border=True):
        st.markdown("#### :material/domain_add: Aggiungi Competitor SEO")
        st.caption(
            "Inserisci il nome e il dominio del competitor da monitorare in SERP. "
            "Se il dominio è già presente nell'anagrafica competitor, verrà abilitato automaticamente anche per il modulo SEO."
        )
        with st.form("form_add_competitor_seo", clear_on_submit=True):
            fc1, fc2, fc3 = st.columns([2, 2.5, 1.2], vertical_alignment="bottom")
            with fc1:
                comp_nome_in = st.text_input(
                    "Nome Competitor *",
                    placeholder="Es. Punto Irrigazione",
                )
            with fc2:
                comp_dom_in = st.text_input(
                    "Dominio / Sito Web *",
                    placeholder="Es. https://www.puntoirrigazione.it",
                )
            with fc3:
                sub_comp_seo = st.form_submit_button(
                    "Aggiungi",
                    type="primary",
                    icon=":material/add:",
                    use_container_width=True,
                )

            if sub_comp_seo:
                if not comp_nome_in.strip() or not comp_dom_in.strip():
                    st.warning(
                        "Inserisci sia il nome che il dominio del competitor.",
                        icon=":material/warning:",
                    )
                else:
                    try:
                        add_competitor_seo(comp_nome_in.strip(), comp_dom_in.strip())
                        st.toast(
                            f"Competitor SEO '{comp_nome_in.strip()}' abilitato!",
                            icon=":material/check_circle:",
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(
                            f"Errore durante il salvataggio del competitor: {exc}",
                            icon=":material/error:",
                        )

    st.divider()

    st.subheader(
        f":material/domain: Competitor Registrati ({len(competitors_seo_attivi)} attivi per SEO su {len(all_competitors)})"
    )

    if not all_competitors:
        st.info("Nessun competitor registrato.", icon=":material/info:")
    else:
        ch = st.columns([2.2, 2.5, 1.5, 1.2], vertical_alignment="center")
        ch[0].caption("**Nome Competitor**")
        ch[1].caption("**Dominio Pulito**")
        ch[2].caption("**Monitoraggio SEO**")
        ch[3].caption("**Elimina**")

        for comp in all_competitors:
            c_id = str(comp["id"])
            c_nome = comp.get("nome") or "Competitor"
            c_dom = comp.get("dominio") or ""
            c_seo_active = bool(comp.get("attivo_seo"))

            with st.container(border=True):
                cc = st.columns([2.2, 2.5, 1.5, 1.2], vertical_alignment="center")
                cc[0].markdown(f"**{c_nome}**")
                cc[1].markdown(f"`{clean_domain(c_dom)}`")

                with cc[2]:
                    toggle_label = "Attivo" if c_seo_active else "Disattivato"
                    new_seo_state = st.toggle(
                        toggle_label,
                        value=c_seo_active,
                        key=f"toggle_seo_comp_{c_id}",
                    )
                    if new_seo_state != c_seo_active:
                        try:
                            update_competitor(c_id, {"attivo_seo": new_seo_state})
                            st.toast(
                                f"Monitoraggio SEO {'attivato' if new_seo_state else 'disattivato'} per {c_nome}.",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Errore aggiornamento stato: {exc}", icon=":material/error:")

                with cc[3]:
                    if st.button(
                        "Elimina",
                        icon=":material/delete:",
                        key=f"btn_del_comp_seo_{c_id}",
                        use_container_width=True,
                    ):
                        try:
                            delete_competitor(c_id)
                            st.toast(
                                f"Competitor '{c_nome}' eliminato.",
                                icon=":material/check_circle:",
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(
                                f"Errore durante l'eliminazione: {exc}",
                                icon=":material/error:",
                            )
